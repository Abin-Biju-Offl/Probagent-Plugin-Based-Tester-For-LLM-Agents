import pytest

from probagent.dq.rules import RulesetError, parse_ruleset


def errors_for(data) -> list[str]:
    with pytest.raises(RulesetError) as caught:
        parse_ruleset(data)
    return caught.value.errors


def test_valid_ruleset():
    ruleset = parse_ruleset({"fields": {"id": {"type": "integer", "min": 1}}})
    assert ruleset.fields["id"].min == 1


def test_typo_in_rule_name_is_caught():
    errors = errors_for({"fields": {"email": {"type": "string", "requird": True}}})
    assert len(errors) == 1
    assert 'Column "email": unknown rule "requird"' in errors[0]


def test_typo_in_setting_is_caught():
    errors = errors_for({"fields": {"a": {}}, "alow_extra_columns": False})
    assert 'Unknown setting "alow_extra_columns"' in errors[0]


@pytest.mark.parametrize("data, expected", [
    ("not an object", "must be a JSON object"),
    ({}, '"fields" is missing'),
    ({"fields": {}}, '"fields" is empty'),
    ({"version": 2, "fields": {"a": {}}}, "version"),
    ({"fields": {"a": {"type": "text"}}}, 'rule "type"'),
    ({"fields": {"a": {"type": "integer", "min_length": 2}}}, "min_length/max_length only work"),
    ({"fields": {"a": {"type": "integer", "pattern": "email"}}}, "pattern only works"),
    ({"fields": {"a": {"type": "string", "min": 1}}}, "min/max only work"),
    ({"fields": {"a": {"type": "integer", "min": 10, "max": 1}}}, "min (10) is bigger than max (1)"),
    ({"fields": {"a": {"type": "integer", "min": "ten"}}}, "min must be a number"),
    ({"fields": {"a": {"type": "string", "min_length": 5, "max_length": 2}}}, "min_length (5)"),
    ({"fields": {"a": {"type": "date", "format": "DD.MM.YY"}}}, "unknown date format"),
    ({"fields": {"a": {"type": "date", "min": "01/01/2026"}}}, "min must be a date"),
    ({"fields": {"a": {"type": "string", "pattern": "postcode"}}}, 'unknown pattern "postcode"'),
    ({"fields": {"a": {"type": "string", "pattern": {"regex": "("}}}}, "regex does not compile"),
    ({"fields": {"a": {"type": "string", "pattern": {"regex": "a" * 201}}}}, "longer than 200"),
    ({"fields": {"a": {"allowed": []}}}, "empty list"),
])
def test_invalid_rulesets(data, expected):
    assert any(expected in error for error in errors_for(data))


def test_all_problems_are_reported_together():
    errors = errors_for({"fields": {
        "a": {"type": "integer", "min": 10, "max": 1},
        "b": {"type": "string", "pattern": "postcode"},
    }})
    assert len(errors) == 2
