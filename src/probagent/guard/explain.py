"""`probagent explain`: why did the guard block (or flag) a tool call?

    probagent explain 20261008-115956-032efe    one guard check, by its run id
    probagent explain                           the latest check that wasn't a clean pass

The agent's block message carries the run id, so a user can pass it on and
someone can look the check up here. Everything comes from the run log:
issues are logged by row, column and code, never with their values.
"""

from typing import Any, Optional

from probagent.guard.log_schema import FEATURE
from probagent.runlog import RunEntry

ACTION_TEXT = {
    "allow": "PASS. The agent got the data unchanged.",
    "block": "FAIL, blocked. The agent did not get this data.",
    "warn": "FAIL, passed on with a warning. The agent got all the data, bad rows included.",
    "filter": "FAIL, filtered. The agent got only the rows without issues.",
}

ERROR_TEXT = {
    "unknown_source": "The tool is mapped to a source that is not in guard.json.",
    "unreadable_data": "The tool's output could not be read as the configured format.",
    "load_failed": "The data was not in the expected shape (for JSON: a list of records).",
    "data_path_not_found": "The data_path in guard.json does not match the tool's output.",
    "unsupported_tool_result": "The tool returned something other than text or records.",
}


def find_entry(entries: list[RunEntry], run_id: Optional[str]) -> Optional[RunEntry]:
    """The guard check with this run id, or the latest one that wasn't a clean pass."""
    checks = [e for e in entries if e.feature == FEATURE and e.action == "check"]
    if run_id is not None:
        return next((e for e in checks if e.run_id == run_id.strip()), None)
    flagged = [e for e in checks if e.status == "error" or e.outputs.get("action") != "allow"]
    return flagged[-1] if flagged else None


def explain(entry: RunEntry) -> str:
    """A plain-text explanation of one guard check."""
    inputs, outputs = entry.inputs, entry.outputs
    lines = [f"Guard check {entry.run_id}  ({entry.started_at})",
             f"Source:  {inputs.get('source')}"]
    if inputs.get("ruleset_name"):
        columns = ", ".join(inputs["columns"]) if inputs.get("columns") else "all"
        lines.append(f"Rules:   {inputs['ruleset_name']} (sha256 {inputs['ruleset_sha256'][:8]}), "
                     f"on_fail {inputs.get('on_fail')}, columns checked: {columns}")
    dataset = inputs.get("dataset")
    if dataset:
        lines.append(f"Data:    {dataset['rows']} rows, {dataset['columns']} columns "
                     f"(sha256 {dataset['sha256'][:8]})")
    lines.append("")

    if entry.status == "error":
        code = entry.error.code if entry.error else "unknown"
        lines += [f"Result:  ERROR, blocked. The guard could not check the data, so the agent "
                  f"did not get it.",
                  f"Why:     {code}: {entry.error.message if entry.error else ''}"]
        if code in ERROR_TEXT:
            lines.append(f"         {ERROR_TEXT[code]}")
        lines += ["", "What to do:",
                  "  - Fix guard.json or the tool so its output matches the source's "
                  "format and data_path.",
                  "  - Check the config with: probagent guard-validate guard.json"]
        return "\n".join(lines)

    action = outputs.get("action", "")
    lines.append(f"Result:  {ACTION_TEXT.get(action, action)}")
    if action == "allow":
        return "\n".join(lines)

    rows_checked = dataset["rows"] if dataset else "?"
    lines.append(f"         {outputs['issue_count']} issues in {outputs['rows_with_issues']} "
                 f"of {rows_checked} rows."
                 + (f" {outputs['rows_removed']} rows removed." if action == "filter" else ""))

    issues = outputs.get("issues", [])
    rules = (inputs.get("ruleset") or {}).get("fields", {})
    if issues:
        lines += ["", f"{'Row':>5}  {'Column':<15} Problem"]
        for issue in issues:
            row = str(issue["row"]) if issue.get("row") is not None else "-"
            column = issue.get("column") or "-"
            lines.append(f"{row:>5}  {column:<15} {issue['code']}: "
                         f"{describe(issue['code'], column, rules.get(column, {}))}")
        if outputs.get("issues_truncated"):
            lines.append(f"  ... only the first {len(issues)} of {outputs['issue_count']} "
                         "issues are logged.")

    lines += ["", "Values are not logged. To see them, check the source data with:",
              "  probagent lint-data <file.csv> --rules <rules file>",
              "", "What to do:",
              "  - If the data is wrong: fix it where it comes from, then ask the agent again.",
              "  - If a rule is wrong: edit the rules file, then restart the agent "
              "(or call guard.reload())."]
    return "\n".join(lines)


def describe(code: str, column: str, rule: dict[str, Any]) -> str:
    """What the issue code means, using the column's actual rule where it helps."""
    if code == "missing_column":
        return "the column is in the rules but not in the data."
    if code == "unexpected_column":
        return 'the column is in the data but not in the rules ("allow_extra_columns": false).'
    if code == "duplicate_row":
        return "an exact copy of an earlier row."
    if code == "missing_value":
        return 'empty, but the rule says "required": true.'
    if code == "wrong_type":
        expected = rule.get("type", "?")
        if expected == "date":
            expected += f" written as {rule.get('format', 'YYYY-MM-DD')}"
        return f'not a valid "type": {expected}.'
    if code == "bad_date":
        return f"written like a date ({rule.get('format', 'YYYY-MM-DD')}) but not a real date."
    if code == "below_min":
        return f'below "min": {rule.get("min")}.'
    if code == "above_max":
        return f'above "max": {rule.get("max")}.'
    if code == "too_short":
        return f'shorter than "min_length": {rule.get("min_length")}.'
    if code == "too_long":
        return f'longer than "max_length": {rule.get("max_length")}.'
    if code == "not_allowed":
        return f'not one of "allowed": {", ".join(rule.get("allowed", []))}.'
    if code == "pattern_mismatch":
        pattern = rule.get("pattern")
        shown = pattern.get("regex") if isinstance(pattern, dict) else pattern
        return f'does not match "pattern": {shown}.'
    if code == "duplicate_value":
        return 'the same value as an earlier row, but the rule says "unique": true.'
    return "see the rules file for this column."
