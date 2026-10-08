"""Only the ticked columns get rules and are checked."""

import json

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from probagent.cli import main
from probagent.dq.infer import infer_ruleset
from probagent.dq.loader import load_csv_bytes
from probagent.dq.rules import parse_ruleset
from probagent.dq.server import create_app
from probagent.dq.validator import check_dataset
from probagent.runlog import RunLog

DIRTY = FIXTURES / "orders_dirty.csv"
RULES = json.loads((FIXTURES / "orders_rules.json").read_text())


def check(csv_text, rules, columns):
    dataset = load_csv_bytes(csv_text.encode(), "t.csv")
    return check_dataset(dataset, parse_ruleset(rules), columns)


# ---------- The check ----------

def test_only_ticked_columns_are_checked(dirty_orders, orders_rules):
    report = check_dataset(dirty_orders, orders_rules, ["quantity", "status"])
    checked = {issue.column for issue in report.issues if issue.column}
    assert checked == {"quantity", "status"}
    assert report.summary.columns_checked == 2
    assert report.columns == ["quantity", "status"]


def test_none_means_all_columns(dirty_orders, orders_rules):
    everything = check_dataset(dirty_orders, orders_rules)
    assert everything.summary.issue_count == 13
    assert everything.columns is None


def test_rules_for_unticked_columns_are_not_reported_missing():
    report = check("a\n1\n", {"fields": {"a": {}, "Date": {"type": "date"}}}, ["a"])
    assert report.issues == []


def test_unexpected_columns_only_among_ticked():
    rules = {"allow_extra_columns": False, "fields": {"a": {}}}
    report = check("a,b,c\n1,2,3\n", rules, ["a", "b"])
    assert [(i.code, i.column) for i in report.issues] == [("unexpected_column", "b")]


def test_duplicate_rows_compare_the_whole_row():
    rules = {"fields": {"a": {}}}
    # Rows differ only in column b, which isn't ticked: not duplicates.
    assert check("a,b\n1,x\n1,y\n", rules, ["a"]).issues == []
    assert [i.code for i in check("a,b\n1,x\n1,x\n", rules, ["a"]).issues] == ["duplicate_row"]


def test_warnings_explain_unchecked_ticked_columns():
    report = check("a,b\n1,2\n", {"fields": {"a": {}}}, ["a", "b", "zzz"])
    assert any("not in the data: zzz" in w for w in report.warnings)
    assert any("no rules, so they were not checked: b" in w for w in report.warnings)


# ---------- Generating rules ----------

def test_rules_are_generated_for_ticked_columns_only(clean_orders):
    ruleset = infer_ruleset(clean_orders, columns=["email", "order_date"]).ruleset
    assert list(ruleset.fields) == ["email", "order_date"]


# ---------- Web API ----------

@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def dataset_id(client):
    return client.post("/api/data/upload", files={
        "file": ("orders_dirty.csv", DIRTY.read_bytes(), "text/csv")}).json()["dataset_id"]


def test_check_endpoint_with_columns(client, dataset_id):
    report = client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": RULES,
                                             "columns": ["email"]}).json()
    assert report["by_column"] == {"email": 2}
    assert report["columns"] == ["email"]


def test_check_endpoint_with_no_ticked_columns(client, dataset_id):
    response = client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": RULES,
                                               "columns": []})
    assert response.status_code == 400 and response.json()["error"] == "no_columns"


def test_infer_endpoint_with_columns(client, dataset_id):
    result = client.post("/api/rules/infer", data={"dataset_id": dataset_id,
                                                   "columns": ["status", "country"]}).json()
    assert list(result["ruleset"]["fields"]) == ["status", "country"]


def test_infer_endpoint_when_ticked_columns_are_not_in_sample(client):
    content = (FIXTURES / "orders_clean.csv").read_bytes()
    response = client.post("/api/rules/infer", files={"file": ("s.csv", content, "text/csv")},
                           data={"columns": ["Date"]})
    assert response.json()["error"] == "no_columns"


def test_ticked_columns_are_in_the_run_log(client, dataset_id):
    client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": RULES,
                                    "columns": ["email"]})
    client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": RULES})
    checks = [e for e in RunLog().read() if e.action == "check"]
    assert checks[0].inputs["columns"] == ["email"]
    assert checks[1].inputs["columns"] is None


# ---------- Terminal ----------

def test_lint_data_columns_option(capsys):
    main(["lint-data", str(DIRTY), "--rules", str(FIXTURES / "orders_rules.json"),
          "--columns", "email, quantity"])
    output = capsys.readouterr().out
    assert "Columns: email, quantity" in output
    assert "6 issues" in output       # 2 email + 3 quantity + 1 duplicate row
