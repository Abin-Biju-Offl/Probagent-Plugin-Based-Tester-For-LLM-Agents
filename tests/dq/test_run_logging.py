"""The data quality feature writes the right run-log entries."""

import json

import httpx
import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from probagent.cli import main
from probagent.dq.fetch import safe_url
from probagent.dq.server import create_app
from probagent.runlog import RunLog, registered_actions

DIRTY = FIXTURES / "orders_dirty.csv"
RULES = json.loads((FIXTURES / "orders_rules.json").read_text())


@pytest.fixture
def client():
    return TestClient(create_app())


def entries() -> list[dict]:
    return [entry.model_dump() for entry in RunLog().read()]


def upload(client):
    return client.post("/api/data/upload",
                       files={"file": ("orders_dirty.csv", DIRTY.read_bytes(), "text/csv")})


def test_all_dq_actions_are_registered():
    actions = {action for feature, action in registered_actions() if feature == "dq"}
    assert actions == {"upload", "fetch", "infer", "check"}


def test_upload_then_check_are_linked(client):
    dataset_id = upload(client).json()["dataset_id"]
    client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": RULES})

    uploaded, checked = entries()
    assert (uploaded["action"], uploaded["status"], uploaded["origin"]) == ("upload", "ok", "web")
    assert uploaded["inputs"]["file_name"] == "orders_dirty.csv"
    assert uploaded["outputs"]["dataset"]["rows"] == 201

    assert checked["action"] == "check" and checked["status"] == "ok"
    assert checked["parent_run_id"] == uploaded["run_id"]
    assert checked["inputs"]["dataset"]["sha256"] == uploaded["inputs"]["sha256"]
    assert checked["inputs"]["ruleset"]["name"] == "orders"
    assert checked["outputs"]["issue_count"] == 13


def test_infer_from_loaded_data_is_linked(client):
    dataset_id = upload(client).json()["dataset_id"]
    client.post("/api/rules/infer", data={"dataset_id": dataset_id})
    uploaded, inferred = entries()
    assert inferred["action"] == "infer" and inferred["parent_run_id"] == uploaded["run_id"]
    assert inferred["outputs"]["fields"] == 8


def test_failures_are_logged_with_a_code(client):
    client.post("/api/data/upload", files={"file": ("empty.csv", b"", "text/csv")})
    client.post("/api/check", json={"dataset_id": "nope", "ruleset": RULES})
    codes = [(e["action"], e["status"], e["error"]["code"]) for e in entries()]
    assert codes == [("upload", "error", "load_failed"), ("check", "error", "dataset_not_found")]


def test_invalid_rules_are_logged(client):
    dataset_id = upload(client).json()["dataset_id"]
    client.post("/api/check", json={"dataset_id": dataset_id,
                                    "ruleset": {"fields": {"a": {"requird": True}}}})
    assert "requird" in entries()[-1]["error"]["message"]


def test_validating_rules_is_not_logged(client):
    client.post("/api/rules/validate", json=RULES)
    assert entries() == []


def test_data_values_are_not_logged(client, log_dir):
    upload(client)
    text = (log_dir / "runs.jsonl").read_text()
    assert "customer1@example.com" not in text   # a cell value from the file


def test_fetch_never_logs_the_token_or_query_string(monkeypatch, log_dir):
    """Fetch through a fake API; the token and ?api_key must not reach the log."""
    def api(request):
        if request.headers.get("authorization") != "Bearer a.b.secret-token":
            return httpx.Response(401)
        return httpx.Response(200, json=[{"id": 1}])

    from probagent.dq import fetch as fetch_module
    real_fetch = fetch_module.fetch_dataset
    monkeypatch.setattr("probagent.dq.server.fetch_dataset",
                        lambda url, path="", token=None: real_fetch(
                            url, path, token, transport=httpx.MockTransport(api)))
    client = TestClient(create_app())
    url = "https://user:pw@api.example.com/orders?api_key=KEY123"
    client.post("/api/data/fetch", json={"url": url})
    client.post("/api/data/fetch", json={"url": url, "token": "a.b.secret-token"})

    first, second = entries()
    assert first["error"]["code"] == "auth_required" and first["inputs"]["token_sent"] is False
    assert second["status"] == "ok" and second["inputs"]["token_sent"] is True
    assert second["inputs"]["url"] == "https://api.example.com/orders"
    text = (log_dir / "runs.jsonl").read_text()
    for secret in ("secret-token", "KEY123", "pw@"):
        assert secret not in text


def test_safe_url():
    assert safe_url("https://u:p@host.com:8443/a/b?key=1#frag") == "https://host.com:8443/a/b"


def test_lint_data_is_logged_from_the_cli(capsys):
    main(["lint-data", str(DIRTY), "--rules", str(FIXTURES / "orders_rules.json")])
    [entry] = entries()
    assert (entry["action"], entry["origin"], entry["status"]) == ("check", "cli", "ok")
    assert entry["outputs"]["issue_count"] == 13


def test_lint_data_failure_is_logged(capsys):
    main(["lint-data", "missing.csv", "--rules", str(FIXTURES / "orders_rules.json")])
    [entry] = entries()
    assert entry["status"] == "error" and entry["error"]["code"] == "file_not_readable"


def test_same_rules_same_fingerprint_whatever_the_formatting(client):
    dataset_id = upload(client).json()["dataset_id"]
    reordered = json.loads(json.dumps(RULES, sort_keys=True))
    client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": RULES})
    client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": reordered})
    first, second = entries()[1:]
    assert first["inputs"]["ruleset_sha256"] == second["inputs"]["ruleset_sha256"]
