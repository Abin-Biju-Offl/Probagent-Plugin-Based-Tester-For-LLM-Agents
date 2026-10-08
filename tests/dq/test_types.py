from datetime import date

import pytest

from probagent.dq.types import (
    is_missing,
    looks_like_date_format,
    matches_pattern,
    parse_boolean,
    parse_date,
    parse_datetime,
    parse_integer,
    parse_number,
)


@pytest.mark.parametrize("text, expected", [
    ("42", 42), ("-3", -3), ("007", 7), (" 5 ", 5),
    ("4.5", None), ("five", None), ("1,000", None), ("", None),
])
def test_parse_integer(text, expected):
    assert parse_integer(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("42", 42.0), ("4.5", 4.5), ("-0.25", -0.25), ("1e3", 1000.0), (".5", 0.5),
    ("abc", None), ("4,5", None), ("1_000", None), ("nan", None), ("inf", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("true", True), ("YES", True), ("1", True), ("false", False), ("No", False),
    ("0", False), ("maybe", None),
])
def test_parse_boolean(text, expected):
    assert parse_boolean(text) == expected


def test_parse_date_needs_matching_format_and_real_date():
    assert parse_date("2026-10-07") == date(2026, 10, 7)
    assert parse_date("07/10/2026", "DD/MM/YYYY") == date(2026, 10, 7)
    assert parse_date("07/10/2026", "MM/DD/YYYY") == date(2026, 7, 10)
    assert parse_date("2026-02-30") is None        # not a real date
    assert parse_date("07/10/2026") is None        # wrong format


def test_date_shape_tells_bad_date_from_wrong_type():
    assert looks_like_date_format("2026-02-30", "YYYY-MM-DD")
    assert not looks_like_date_format("07/10/2026", "YYYY-MM-DD")


def test_parse_datetime():
    assert parse_datetime("2026-10-07T09:30:00") is not None
    assert parse_datetime("2026-10-07T09:30:00Z") is not None
    assert parse_datetime("yesterday") is None


def test_missing_values():
    assert is_missing("") and is_missing("   ")
    assert not is_missing("0")


@pytest.mark.parametrize("text, pattern, expected", [
    ("a@b.com", "email", True), ("john@", "email", False),
    ("+91 98765 43210", "phone", True), ("call me", "phone", False),
    ("https://example.com/x", "url", True), ("example.com", "url", False),
    ("123e4567-e89b-12d3-a456-426614174000", "uuid", True), ("1234", "uuid", False),
    ("ABC-12", {"regex": r"^[A-Z]{3}-\d+$"}, True), ("abc-12", {"regex": r"^[A-Z]{3}-\d+$"}, False),
])
def test_patterns(text, pattern, expected):
    assert matches_pattern(text, pattern) == expected
