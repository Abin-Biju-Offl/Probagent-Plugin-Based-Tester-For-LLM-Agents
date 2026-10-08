import json

from conftest import FIXTURES
from fastapi.testclient import TestClient

from probagent.cli import main
from probagent.dq.server import create_app

DIRTY = str(FIXTURES / "orders_dirty.csv")
CLEAN = str(FIXTURES / "orders_clean.csv")
RULES = str(FIXTURES / "orders_rules.json")


def test_lint_data_finds_issues(capsys):
    assert main(["lint-data", DIRTY, "--rules", RULES]) == 1
    output = capsys.readouterr().out
    assert "13 issues" in output
    assert "duplicate_row" in output


def test_lint_data_clean_file(capsys):
    assert main(["lint-data", CLEAN, "--rules", RULES]) == 0
    assert "0 issues" in capsys.readouterr().out


def test_lint_data_invalid_rules(tmp_path, capsys):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"fields": {"a": {"requird": True}}}))
    assert main(["lint-data", CLEAN, "--rules", str(rules)]) == 3
    assert "requird" in capsys.readouterr().err


def test_lint_data_missing_file(capsys):
    assert main(["lint-data", "no_such_file.csv", "--rules", RULES]) == 3


def test_lint_data_matches_web_report(capsys):
    """Phase 5: the terminal and the web page use the same engine."""
    client = TestClient(create_app())
    with open(DIRTY, "rb") as file:
        dataset_id = client.post("/api/data/upload", files={"file": ("orders_dirty.csv", file)}
                                 ).json()["dataset_id"]
    report = client.post("/api/check", json={
        "dataset_id": dataset_id, "ruleset": json.loads(open(RULES).read())}).json()

    main(["lint-data", DIRTY, "--rules", RULES])
    output = capsys.readouterr().out
    for issue in report["issues"]:
        assert issue["message"] in output
