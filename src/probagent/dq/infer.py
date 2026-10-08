"""Guessing a ruleset from sample data (spec Section 7).

    result = infer_ruleset(dataset)
    result.ruleset   # a draft the user MUST review before using
    result.notes     # advice per column, e.g. "widen min/max"

The guesses are only as good as the sample, which is why every rule that
could be too strict comes with a note.
"""

from typing import Optional

from probagent.dq import settings
from probagent.dq.models import Dataset, FieldRule, InferResult, Ruleset
from probagent.dq.types import (
    DATE_FORMATS,
    NAMED_PATTERNS,
    is_missing,
    parse_boolean,
    parse_date,
    parse_datetime,
    parse_integer,
    parse_number,
)

SAMPLE_KEY = "(sample)"   # notes about the whole sample, not one column


def infer_ruleset(dataset: Dataset, name: str = "inferred_rules",
                  columns: Optional[list[str]] = None) -> InferResult:
    """Guess rules for every column, or only for `columns` (the ticked ones)."""
    sample_rows = dataset.rows[: settings.INFER_SAMPLE_ROWS]
    fields: dict[str, FieldRule] = {}
    notes: dict[str, list[str]] = {}

    if len(dataset.rows) > settings.INFER_SAMPLE_ROWS:
        notes[SAMPLE_KEY] = [f"Rules were guessed from the first {settings.INFER_SAMPLE_ROWS:,} "
                             f"of {len(dataset.rows):,} rows."]

    for index, column in enumerate(dataset.columns):
        if columns is not None and column not in columns:
            continue
        values = [row[index] for row in sample_rows]
        rule, column_notes = infer_field(values)
        fields[column] = rule
        if column_notes:
            notes[column] = column_notes

    return InferResult(ruleset=Ruleset(name=name, fields=fields), notes=notes)


def infer_field(values: list[str]) -> tuple[FieldRule, list[str]]:
    """Guess the rules for one column from its sample values."""
    notes: list[str] = []
    filled = [v.strip() for v in values if not is_missing(v)]
    empty_count = len(values) - len(filled)

    if not filled:
        return FieldRule(), ["Every value in the sample is empty. Left as optional text."]

    empty_pct = empty_count / len(values) * 100
    if empty_pct >= 50:
        notes.append(f"Mostly empty ({empty_pct:.0f}%). Left as optional.")

    rule = FieldRule(required=(empty_count == 0))
    rule.type, rule.format, type_share = guess_type(filled, notes)
    if type_share < 1:
        notes.append(f"{(1 - type_share) * 100:.0f}% of values didn't look like "
                     f"type \"{rule.type}\".")

    # Dates get only their type and format, no min/max: new data keeps adding
    # later dates, and a max taken from the sample would reject all of them.
    if rule.type in ("integer", "number"):
        add_min_max(rule, filled, notes)
    if rule.type == "string":
        add_text_rules(rule, filled, notes)

    # Unique: only for IDs-like columns (whole numbers or text). Prices and
    # dates are often all different in a sample without needing to be unique.
    distinct = set(filled)
    if (rule.type in ("integer", "string") and rule.allowed is None
            and len(values) >= 10 and len(distinct) == len(filled)):
        rule.unique = True
        notes.append("Every value in the sample was different, so \"unique\" was set. "
                     "Remove it if repeats are allowed.")

    return rule, notes


def guess_type(filled: list[str], notes: list[str]) -> tuple[str, Optional[str], float]:
    """Try types in order: boolean, integer, number, date, datetime, string.

    Returns (type, date format or None, share of values that fit).
    """
    for type_name, parser in (("boolean", parse_boolean), ("integer", parse_integer),
                              ("number", parse_number)):
        share = share_matching(filled, lambda v: parser(v) is not None)
        if share >= settings.INFER_MATCH_SHARE:
            return type_name, None, share

    date_format, share = guess_date_format(filled, notes)
    if date_format is not None:
        return "date", date_format, share

    share = share_matching(filled, lambda v: parse_datetime(v) is not None)
    if share >= settings.INFER_MATCH_SHARE:
        return "datetime", None, share

    return "string", None, 1.0


def guess_date_format(filled: list[str], notes: list[str]) -> tuple[Optional[str], float]:
    """Pick the date format that fits the most values (if enough values fit)."""
    shares = {name: share_matching(filled, lambda v, f=name: parse_date(v, f) is not None)
              for name in DATE_FORMATS}
    best = max(shares, key=shares.get)     # on a tie, the first format in the list wins
    if shares[best] < settings.INFER_MATCH_SHARE:
        return None, 0.0

    # Every value fits both day/month and month/day: we can't tell which one is meant.
    if best == "DD/MM/YYYY" and shares["MM/DD/YYYY"] == shares[best]:
        notes.append("Dates like 03/04/2026 could be day/month or month/day. "
                     "DD/MM/YYYY was chosen. Change \"format\" if that's wrong.")
    return best, shares[best]


def add_min_max(rule: FieldRule, filled: list[str], notes: list[str]) -> None:
    """Set min/max to the smallest and largest numbers seen."""
    parser = parse_integer if rule.type == "integer" else parse_number
    numbers = [n for n in (parser(v) for v in filled) if n is not None]
    rule.min, rule.max = min(numbers), max(numbers)
    notes.append(f"min/max set to {rule.min} – {rule.max} from the sample. "
                 "Widen these if other values are valid.")


def add_text_rules(rule: FieldRule, filled: list[str], notes: list[str]) -> None:
    """For text columns: a known pattern, a short list of allowed values, or a fixed length."""
    # A known pattern such as email?
    for pattern_name, regex in NAMED_PATTERNS.items():
        share = share_matching(filled, lambda v: regex.match(v) is not None)
        if share >= settings.INFER_MATCH_SHARE:
            rule.pattern = pattern_name
            if share < 1:
                notes.append(f"{(1 - share) * 100:.0f}% of values didn't match the "
                             f"{pattern_name} pattern.")
            return

    # A few repeated values, like a status column?
    distinct = sorted(set(filled))
    if (len(distinct) <= settings.INFER_MAX_ALLOWED_VALUES
            and len(distinct) <= len(filled) * 0.5):
        rule.allowed = distinct
        notes.append(f"\"allowed\" list built from the {len(distinct)} values seen. "
                     "Add any values the sample didn't contain.")

    # All values the same length, like 2-letter country codes?
    lengths = {len(v) for v in filled}
    if len(lengths) == 1 and len(filled) > 1:
        rule.min_length = rule.max_length = lengths.pop()


def share_matching(values: list[str], test) -> float:
    """What fraction of the values pass the test? (0.0 to 1.0)"""
    return sum(1 for v in values if test(v)) / len(values)
