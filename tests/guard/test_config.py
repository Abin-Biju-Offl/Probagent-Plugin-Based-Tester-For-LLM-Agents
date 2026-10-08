"""guard.json loading: every mistake is found when the guard starts."""

import json

import pytest

from probagent.cli import main
from probagent.guard import DataGuard, GuardConfigError

from guard_helpers import RULES_FILE, write_config


def write(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def config_errors(path) -> list[str]:
    with pytest.raises(GuardConfigError) as error:
        DataGuard.from_config(path)
    return error.value.errors


def test_valid_config_loads(tmp_path):
    guard = DataGuard.from_config(write_config(tmp_path, on_fail="warn"))
    assert list(guard.sources) == ["orders"]
    source = guard.sources["orders"]
    assert source.policy.on_fail == "warn"
    assert source.ruleset.name == "orders"
    assert len(source.ruleset_sha256) == 64


def test_rules_path_is_relative_to_the_config(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "orders.json").write_text(RULES_FILE.read_text(), encoding="utf-8")
    path = write(tmp_path / "guard.json",
                 {"sources": {"orders": {"rules": "rules/orders.json"}}})
    assert DataGuard.from_config(path).sources["orders"].policy.on_fail == "block"


def test_missing_config_file(tmp_path):
    assert config_errors(tmp_path / "nope.json") == [
        f"Could not read the guard config {tmp_path / 'nope.json'}."]


def test_config_not_json(tmp_path):
    errors = config_errors(write(tmp_path / "guard.json", "{not json"))
    assert errors[0].startswith("guard.json is not valid JSON")


@pytest.mark.parametrize("config, expected", [
    ([], "The guard config must be a JSON object"),
    ({}, '"sources" is missing'),
    ({"sources": {}}, '"sources" is empty'),
    ({"sources": {"orders": {"rules": "r.json", "on_fial": "warn"}}},
     'Source "orders": unknown setting "on_fial"'),
    ({"sources": {"orders": {"rules": "r.json", "on_fail": "explode"}}},
     'Source "orders": "on_fail"'),
    ({"sources": {"orders": {"rules": "r.json", "max_bad_rows_pct": 150}}},
     'Source "orders": "max_bad_rows_pct"'),
    ({"version": 2, "sources": {}}, '"version"'),
])
def test_bad_config_shapes(tmp_path, config, expected):
    errors = config_errors(write(tmp_path / "guard.json", config))
    assert errors[0].startswith(expected), errors


def test_missing_rules_file(tmp_path):
    errors = config_errors(write(tmp_path / "guard.json",
                                 {"sources": {"orders": {"rules": "missing.json"}}}))
    assert errors == ['Source "orders": could not read the rules file missing.json.']


def test_invalid_rules_are_reported_per_source(tmp_path):
    write(tmp_path / "bad.json", {"fields": {"qty": {"type": "integer", "requird": True}}})
    errors = config_errors(write(tmp_path / "guard.json",
                                 {"sources": {"orders": {"rules": "bad.json"}}}))
    assert len(errors) == 1
    assert errors[0].startswith('Source "orders": bad.json: Column "qty": unknown rule "requird"')


def test_all_sources_are_checked_before_failing(tmp_path):
    write(tmp_path / "not_json.json", "{")
    errors = config_errors(write(tmp_path / "guard.json", {"sources": {
        "a": {"rules": "missing.json"},
        "b": {"rules": "not_json.json"},
        "c": {"rules": str(RULES_FILE)},
    }}))
    assert [e.split(":")[0] for e in errors] == ['Source "a"', 'Source "b"']


def test_columns_must_have_rules(tmp_path):
    errors = config_errors(write_config(tmp_path, columns=["email", "emial"]))
    assert errors == [f'Source "orders": these columns have no rules in {RULES_FILE}: emial']


def test_empty_columns_list(tmp_path):
    errors = config_errors(write_config(tmp_path, columns=[]))
    assert errors == ['Source "orders": "columns" is an empty list, so nothing would be checked.']


def test_data_path_needs_json(tmp_path):
    errors = config_errors(write_config(tmp_path, format="csv", data_path="data"))
    assert errors == ['Source "orders": data_path only works with format "json".']


def test_reload_picks_up_new_rules_and_keeps_old_ones_on_error(tmp_path, clean_records):
    rules = tmp_path / "rules.json"
    rules.write_text(RULES_FILE.read_text(), encoding="utf-8")
    path = write(tmp_path / "guard.json", {"sources": {"orders": {"rules": "rules.json"}}})
    guard = DataGuard.from_config(path)
    assert guard.check(clean_records, "orders").verdict == "pass"

    # Tighter rules: no order may have a quantity above 1.
    data = json.loads(rules.read_text())
    data["fields"]["quantity"]["max"] = 1
    write(rules, data)
    guard.reload()
    assert guard.check(clean_records, "orders").verdict == "fail"

    # Broken rules: reload fails and the guard keeps the rules it had.
    write(rules, "{")
    with pytest.raises(GuardConfigError):
        guard.reload()
    assert guard.check(clean_records, "orders").verdict == "fail"


# ---------- probagent guard-validate ----------

def test_cli_valid_config(tmp_path, capsys):
    assert main(["guard-validate", str(write_config(tmp_path, on_fail="filter"))]) == 0
    out = capsys.readouterr().out
    assert "guard.json is valid" in out
    assert 'orders: rules "orders" (8 columns), format json, on_fail filter' in out


def test_cli_invalid_config(tmp_path, capsys):
    assert main(["guard-validate", str(write_config(tmp_path, columns=["nope"]))]) == 3
    assert "these columns have no rules" in capsys.readouterr().err
