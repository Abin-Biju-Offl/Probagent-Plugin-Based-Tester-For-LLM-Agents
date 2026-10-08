"""Reading and checking a ruleset (spec Section 6.5).

A ruleset is checked BEFORE it is used, so mistakes in the rules are shown
to the user instead of producing a confusing report.

    ruleset = parse_ruleset(json_data)      # raises RulesetError if invalid
"""

import re
from typing import Any

from pydantic import ValidationError

from probagent.dq import settings
from probagent.dq.models import FieldRule, Ruleset
from probagent.dq.types import DATE_FORMATS, NAMED_PATTERNS, parse_date

NUMERIC_TYPES = {"integer", "number"}


class RulesetError(Exception):
    """The ruleset is invalid. `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_ruleset(data: Any) -> Ruleset:
    """Turn JSON data into a Ruleset, or raise RulesetError with all problems."""
    if not isinstance(data, dict):
        raise RulesetError(["The ruleset must be a JSON object: { ... }"])

    try:
        ruleset = Ruleset.model_validate(data)
    except ValidationError as error:
        raise RulesetError([_friendly_message(e) for e in error.errors()]) from None

    errors = []
    if not ruleset.fields:
        errors.append('"fields" is empty. Add at least one column rule.')
    for column, rule in ruleset.fields.items():
        errors.extend(f'Column "{column}": {problem}' for problem in check_field_rule(rule))

    if errors:
        raise RulesetError(errors)
    return ruleset


def check_field_rule(rule: FieldRule) -> list[str]:
    """Check that one column's rules make sense together."""
    problems = []

    # Rules must fit the type.
    if rule.min_length is not None or rule.max_length is not None:
        if rule.type != "string":
            problems.append("min_length/max_length only work with type \"string\".")
    if rule.pattern is not None and rule.type != "string":
        problems.append('pattern only works with type "string".')
    if rule.format is not None and rule.type != "date":
        problems.append('format only works with type "date".')
    if (rule.min is not None or rule.max is not None) and rule.type not in (
        "integer", "number", "date"
    ):
        problems.append('min/max only work with types "integer", "number" and "date".')

    # Date format must be one we know.
    if rule.type == "date" and rule.format is not None and rule.format not in DATE_FORMATS:
        problems.append(f'unknown date format "{rule.format}". Use one of: '
                        + ", ".join(DATE_FORMATS))
        return problems  # min/max below can't be checked without a valid format

    # min/max must be the right kind of value, and min <= max.
    low = _bound_value(rule, rule.min, "min", problems)
    high = _bound_value(rule, rule.max, "max", problems)
    if low is not None and high is not None and low > high:
        problems.append(f"min ({rule.min}) is bigger than max ({rule.max}).")

    # Lengths
    for name in ("min_length", "max_length"):
        value = getattr(rule, name)
        if value is not None and value < 0:
            problems.append(f"{name} can't be negative.")
    if (rule.min_length is not None and rule.max_length is not None
            and rule.min_length > rule.max_length):
        problems.append(f"min_length ({rule.min_length}) is bigger than "
                        f"max_length ({rule.max_length}).")

    if rule.allowed is not None and len(rule.allowed) == 0:
        problems.append('"allowed" is an empty list, so no value could pass.')

    if rule.pattern is not None:
        problems.extend(_check_pattern(rule.pattern))

    return problems


def _bound_value(rule: FieldRule, value, name: str, problems: list[str]):
    """Return min/max as a comparable value, or None. Adds a problem if invalid."""
    if value is None:
        return None
    if rule.type in NUMERIC_TYPES:
        if isinstance(value, str):
            problems.append(f"{name} must be a number, got \"{value}\".")
            return None
        return value
    if rule.type == "date":
        date_format = rule.format or "YYYY-MM-DD"
        parsed = parse_date(str(value), date_format)
        if parsed is None:
            problems.append(f'{name} must be a date written as {date_format}, got "{value}".')
        return parsed
    return None


def _check_pattern(pattern) -> list[str]:
    if isinstance(pattern, str):
        if pattern not in NAMED_PATTERNS:
            return [f'unknown pattern "{pattern}". Use one of: '
                    + ", ".join(NAMED_PATTERNS) + ', or {"regex": "..."}.']
        return []

    if set(pattern) != {"regex"}:
        return ['a custom pattern must look like {"regex": "..."}.']
    regex = pattern["regex"]
    if len(regex) > settings.MAX_REGEX_CHARS:
        return [f"regex is longer than {settings.MAX_REGEX_CHARS} characters."]
    try:
        re.compile(regex)
    except re.error as error:
        return [f"regex does not compile: {error}."]
    return []


def _friendly_message(error: dict) -> str:
    """Turn a Pydantic error into a sentence a beginner can act on."""
    location = [str(part) for part in error["loc"]]

    # A rule name that doesn't exist, e.g. "requird"
    if error["type"] == "extra_forbidden":
        unknown = location[-1]
        if len(location) >= 3 and location[0] == "fields":
            return (f'Column "{location[1]}": unknown rule "{unknown}". Known rules: '
                    + ", ".join(FieldRule.model_fields) + ".")
        return f'Unknown setting "{unknown}". Known settings: ' + ", ".join(
            Ruleset.model_fields) + "."

    if location and location[0] == "fields" and len(location) >= 3:
        # Pydantic adds union branch names like "int" to the location; keep the rule name only.
        return f'Column "{location[1]}", rule "{location[2]}": {error["msg"]}.'
    if location == ["fields"] and error["type"] == "missing":
        return '"fields" is missing. Add a "fields" object with one entry per column.'
    return f'{".".join(location) or "ruleset"}: {error["msg"]}.'
