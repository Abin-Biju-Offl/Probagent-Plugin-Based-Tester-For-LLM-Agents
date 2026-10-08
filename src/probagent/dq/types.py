"""Turning text into typed values (spec Section 6.2).

Each `parse_*` function takes a text value and returns the typed value,
or None when the text is not valid for that type.
"""

import re
from datetime import date, datetime
from typing import Optional, Union

from probagent.dq import settings

# The date formats a ruleset may use, mapped to Python's strptime codes.
# The order matters for inference: DD/MM/YYYY comes before MM/DD/YYYY,
# so it wins when a date like 03/04/2026 could be either.
DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY/MM/DD": "%Y/%m/%d",
}
DEFAULT_DATE_FORMAT = "YYYY-MM-DD"

# Built-in patterns a ruleset can refer to by name, e.g. "pattern": "email".
NAMED_PATTERNS = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "phone": re.compile(r"^\+?[0-9][0-9 ()-]{5,18}[0-9]$"),
    "url": re.compile(r"^https?://[^\s/$.?#][^\s]*$"),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
}

INTEGER_RE = re.compile(r"^[+-]?\d+$")
NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
TRUE_WORDS = {"true", "yes", "1"}
FALSE_WORDS = {"false", "no", "0"}


def is_missing(text: str) -> bool:
    """Empty or spaces-only cells count as missing."""
    return text.strip() == ""


def parse_integer(text: str) -> Optional[int]:
    """'42', '-3' and '007' are integers. '4.5' and '1,000' are not."""
    text = text.strip()
    return int(text) if INTEGER_RE.match(text) else None


def parse_number(text: str) -> Optional[float]:
    """'42', '4.5', '-0.25' and '1e3' are numbers. 'abc' and '4,5' are not."""
    text = text.strip()
    return float(text) if NUMBER_RE.match(text) else None


def parse_boolean(text: str) -> Optional[bool]:
    word = text.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return None


def parse_date(text: str, format_name: str = DEFAULT_DATE_FORMAT) -> Optional[date]:
    """Returns a date only if the text matches the format AND is a real date."""
    try:
        return datetime.strptime(text.strip(), DATE_FORMATS[format_name]).date()
    except ValueError:
        return None


def looks_like_date_format(text: str, format_name: str) -> bool:
    """Does the text have the right shape, even if the date is impossible?

    "2026-02-30" has the shape of YYYY-MM-DD but is not a real date.
    We use this to tell `bad_date` (impossible date) apart from
    `wrong_type` (not written as a date at all).
    """
    shape = re.escape(format_name)
    shape = shape.replace("YYYY", r"\d{4}").replace("MM", r"\d{1,2}").replace("DD", r"\d{1,2}")
    return re.fullmatch(shape, text.strip()) is not None


def parse_datetime(text: str) -> Optional[datetime]:
    """ISO 8601, e.g. '2026-10-07T09:30:00'. A trailing 'Z' means UTC."""
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_value(text: str, field_type: str, date_format: Optional[str] = None
                ) -> Optional[Union[str, int, float, bool, date, datetime]]:
    """Parse text as the given type. Returns None if it doesn't fit."""
    if field_type == "string":
        return text
    if field_type == "integer":
        return parse_integer(text)
    if field_type == "number":
        return parse_number(text)
    if field_type == "boolean":
        return parse_boolean(text)
    if field_type == "date":
        return parse_date(text, date_format or DEFAULT_DATE_FORMAT)
    if field_type == "datetime":
        return parse_datetime(text)
    raise ValueError(f"Unknown type: {field_type}")


def matches_pattern(text: str, pattern: Union[str, dict]) -> bool:
    """Check text against a named pattern ("email") or {"regex": "..."}."""
    if isinstance(pattern, str):
        return NAMED_PATTERNS[pattern].match(text.strip()) is not None
    # Custom regex. Values are cut short to limit slow-regex risk (spec 6.5).
    short_text = text.strip()[: settings.MAX_VALUE_CHARS_FOR_REGEX]
    return re.search(pattern["regex"], short_text) is not None
