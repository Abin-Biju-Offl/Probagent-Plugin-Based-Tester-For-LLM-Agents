"""The check: every row against every rule (spec Section 8).

    report = check_dataset(dataset, ruleset)

Order of checks:
  1. Dataset level: missing columns, unexpected columns, duplicate rows.
  2. Each cell: missing -> type -> min/max -> length -> allowed -> pattern -> unique.
     A cell gets at most ONE issue (the first rule it breaks), so the report
     stays easy to read.
"""

from collections import Counter
from datetime import datetime
from typing import Optional

from probagent.dq import settings
from probagent.dq.models import Dataset, FieldRule, Issue, Report, ReportSummary, Ruleset
from probagent.dq.types import (
    DEFAULT_DATE_FORMAT,
    is_missing,
    looks_like_date_format,
    matches_pattern,
    parse_date,
    parse_value,
)

TYPE_NAMES = {
    "integer": "a whole number",
    "number": "a number",
    "boolean": "true/false",
    "datetime": "a date-time (ISO 8601, e.g. 2026-10-07T09:30:00)",
}


def check_dataset(dataset: Dataset, ruleset: Ruleset,
                  columns: Optional[list[str]] = None) -> Report:
    """Check the dataset and build the report.

    columns: the names of the ticked columns to check. None means all columns.
    Rules for columns that aren't ticked are ignored.
    """
    issues, columns_checked, warnings = find_issues(dataset, ruleset, columns)
    report = build_report(dataset, ruleset, issues, columns_checked, warnings)
    report.columns = columns
    return report


def find_issues(dataset: Dataset, ruleset: Ruleset,
                columns: Optional[list[str]] = None) -> tuple[list[Issue], int, list[str]]:
    """Every issue in the dataset, not cut to the report limit.

    Returns (issues, number of columns checked, warnings). The data guard uses
    the full list to know exactly which rows to remove.
    """
    if columns is None:
        columns_in_scope = dataset.columns
        fields = ruleset.fields
        warnings = list(dataset.warnings)
    else:
        columns_in_scope = [name for name in dataset.columns if name in columns]
        fields = {name: rule for name, rule in ruleset.fields.items() if name in columns}
        warnings = list(dataset.warnings) + selection_warnings(dataset, ruleset, columns)

    issues = check_columns(columns_in_scope, fields, ruleset.allow_extra_columns)

    # Remember where each column is, so we can find a cell by column name.
    column_index = {name: i for i, name in enumerate(dataset.columns)}
    checked_fields = {name: rule for name, rule in fields.items() if name in column_index}

    seen_rows: dict[tuple, int] = {}                        # full row -> first row number
    seen_values = {name: {} for name, rule in checked_fields.items() if rule.unique}

    for row_number, row in enumerate(dataset.rows, start=1):
        # An exact copy of an earlier row gets one issue. Its cells were
        # already checked on the first copy, so we skip them here.
        # The whole row is compared, whichever columns are ticked.
        if ruleset.check_duplicate_rows:
            key = tuple(row)
            if key in seen_rows:
                issues.append(Issue(
                    row=row_number, code="duplicate_row",
                    message=f"Row {row_number} is an exact copy of row {seen_rows[key]}."))
                continue
            seen_rows[key] = row_number

        for name, rule in checked_fields.items():
            value = row[column_index[name]]
            issue = check_cell(value, name, rule)

            if issue is None and rule.unique and not is_missing(value):
                issue = check_unique(value, name, row_number, seen_values[name])

            if issue is not None:
                issue.row = row_number
                issues.append(issue)

    return issues, len(checked_fields), warnings


def check_columns(data_columns: list[str], fields: dict[str, FieldRule],
                  allow_extra_columns: bool) -> list[Issue]:
    """Dataset-level checks: are the expected columns there?"""
    issues = []
    for name in fields:
        if name not in data_columns:
            issues.append(Issue(column=name, code="missing_column",
                                message=f'Column "{name}" is in the rules but not in the data.'))
    if not allow_extra_columns:
        for name in data_columns:
            if name not in fields:
                issues.append(Issue(column=name, code="unexpected_column",
                                    message=f'Column "{name}" is not in the rules.'))
    return issues


def selection_warnings(dataset: Dataset, ruleset: Ruleset, columns: list[str]) -> list[str]:
    """Explain ticked columns that couldn't be checked."""
    warnings = []
    unknown = [name for name in columns if name not in dataset.columns]
    if unknown:
        warnings.append("These ticked columns are not in the data: " + ", ".join(unknown))
    no_rules = [name for name in columns if name in dataset.columns
                and name not in ruleset.fields]
    if no_rules:
        warnings.append("These ticked columns have no rules, so they were not checked: "
                        + ", ".join(no_rules))
    return warnings


def check_cell(value: str, column: str, rule: FieldRule) -> Optional[Issue]:
    """Check one value against its column's rules. Returns the first problem, or None."""

    def problem(code: str, message: str) -> Issue:
        return Issue(column=column, value=shorten(value), code=code, message=message)

    # 1. Missing
    if is_missing(value):
        if rule.required:
            return problem("missing_value", f'"{column}" is required but empty.')
        return None   # optional and empty: nothing else to check

    text = value.strip()

    # 2. Type
    date_format = rule.format or DEFAULT_DATE_FORMAT
    parsed = parse_value(text, rule.type, date_format)
    if parsed is None:
        if rule.type == "date" and looks_like_date_format(text, date_format):
            return problem("bad_date", f'"{column}" value "{shorten(text)}" is not a real date.')
        if rule.type == "date":
            return problem("wrong_type", f'"{column}" should be a date written as '
                                         f'{date_format}, got "{shorten(text)}".')
        return problem("wrong_type", f'"{column}" should be {TYPE_NAMES[rule.type]}, '
                                     f'got "{shorten(text)}".')

    # 3. min / max (numbers and dates)
    low, high = rule.min, rule.max
    if rule.type == "date":
        low = parse_date(str(low), date_format) if low is not None else None
        high = parse_date(str(high), date_format) if high is not None else None
    if low is not None and parsed < low:
        return problem("below_min", f'"{column}" is {text}, below the minimum of {rule.min}.')
    if high is not None and parsed > high:
        return problem("above_max", f'"{column}" is {text}, above the maximum of {rule.max}.')

    # 4. Text length
    if rule.min_length is not None and len(text) < rule.min_length:
        return problem("too_short", f'"{column}" has {len(text)} characters, '
                                    f"minimum is {rule.min_length}.")
    if rule.max_length is not None and len(text) > rule.max_length:
        return problem("too_long", f'"{column}" has {len(text)} characters, '
                                   f"maximum is {rule.max_length}.")

    # 5. Allowed values
    if rule.allowed is not None and text not in [a.strip() for a in rule.allowed]:
        return problem("not_allowed", f'"{column}" is "{shorten(text)}", allowed: '
                                      + ", ".join(rule.allowed) + ".")

    # 6. Pattern
    if rule.pattern is not None and not matches_pattern(text, rule.pattern):
        if isinstance(rule.pattern, str):
            what = f"is not a valid {rule.pattern}"          # e.g. "is not a valid email"
        else:
            what = f"does not match the pattern {rule.pattern['regex']}"
        return problem("pattern_mismatch", f'"{column}" value "{shorten(text)}" {what}.')

    return None


def check_unique(value: str, column: str, row_number: int,
                 seen: dict[str, int]) -> Optional[Issue]:
    """Report a value that already appeared in an earlier row of this column."""
    text = value.strip()
    if text in seen:
        return Issue(column=column, value=shorten(text), code="duplicate_value",
                     message=f'"{column}" value "{shorten(text)}" already appears in '
                             f"row {seen[text]}.")
    seen[text] = row_number
    return None


def shorten(text: str) -> str:
    """Cut long values so the report stays readable."""
    limit = settings.MAX_VALUE_CHARS_IN_REPORT
    return text if len(text) <= limit else text[:limit] + "…"


def build_report(dataset: Dataset, ruleset: Ruleset, issues: list[Issue],
                 columns_checked: int, warnings: list[str]) -> Report:
    """Count the issues and keep only the first few hundred for display."""
    # Dataset-level issues (no row) first, then by row number.
    issues.sort(key=lambda issue: issue.row or 0)

    rows_with_issues = len({issue.row for issue in issues if issue.row is not None})
    total_rows = len(dataset.rows)
    clean_pct = 100.0 if total_rows == 0 else (total_rows - rows_with_issues) / total_rows * 100

    return Report(
        source=dataset.source,
        ruleset_name=ruleset.name,
        checked_at=datetime.now().isoformat(timespec="seconds"),
        summary=ReportSummary(
            rows_checked=total_rows,
            columns_checked=columns_checked,
            issue_count=len(issues),
            rows_with_issues=rows_with_issues,
            clean_rows_pct=round(clean_pct, 1),
        ),
        by_column=dict(Counter(issue.column for issue in issues if issue.column)),
        by_code=dict(Counter(issue.code for issue in issues)),
        issues=issues[: settings.MAX_ISSUES_SHOWN],
        truncated=len(issues) > settings.MAX_ISSUES_SHOWN,
        warnings=warnings,
    )
