"""Web API tests using FastAPI's TestClient (no real server needed)."""

import json

import httpx
import pytest
from conftest import FIXTURES, seeded_issues
from fastapi.testclient import TestClient

from probagent.dq import fetch as fetch_module
from probagent.dq import settings
from probagent.dq.server import create_app

DEMO_TOKEN = "demo.header.token"


@pytest.fixture
def client():
    return TestClient(create_app(demo=True, demo_token=DEMO_TOKEN))


def upload(client, name="orders_dirty.csv"):
    content = (FIXTURES / name).read_bytes()
    return client.post("/api/data/upload", files={"file": (name, content, "text/csv")})


def orders_rules() -> dict:
    return json.loads((FIXTURES / "orders_rules.json").read_text())


def test_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Data Quality Check" in response.text


def test_upload_check_flow(client):
    data = upload(client).json()
    assert data["row_count"] == 201
    assert len(data["preview"]) == 10

    report = client.post("/api/check", json={"dataset_id": data["dataset_id"],
                                             "ruleset": orders_rules()}).json()
    found = {(i["row"], i["column"] or "-", i["code"]) for i in report["issues"]}
    assert found == seeded_issues()


def test_script_in_data_comes_back_as_plain_text(client):
    """The page shows values with textContent; the API must not change them."""
    content = b"note\n<script>alert(1)</script>\n"
    data = client.post("/api/data/upload",
                       files={"file": ("x.csv", content, "text/csv")}).json()
    assert data["preview"] == [["<script>alert(1)</script>"]]


def test_bad_upload(client):
    response = client.post("/api/data/upload", files={"file": ("x.csv", b"", "text/csv")})
    assert response.status_code == 400
    assert response.json()["error"] == "load_failed"


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_BYTES", 100)
    response = client.post("/api/data/upload",
                           files={"file": ("x.csv", b"a\n" + b"1\n" * 100, "text/csv")})
    assert "larger than" in response.json()["message"]


def test_infer_from_sample_file_and_from_loaded_data(client):
    content = (FIXTURES / "orders_clean.csv").read_bytes()
    from_file = client.post("/api/rules/infer",
                            files={"file": ("s.csv", content, "text/csv")}).json()
    dataset_id = upload(client, "orders_clean.csv").json()["dataset_id"]
    from_data = client.post("/api/rules/infer", data={"dataset_id": dataset_id}).json()

    assert from_file["ruleset"] == from_data["ruleset"]
    assert from_file["ruleset"]["fields"]["email"]["pattern"] == "email"
    assert "quantity" in from_file["notes"]
    assert "min_length" not in from_file["ruleset"]["fields"]["email"]   # None values left out


def test_infer_needs_a_sample(client):
    assert client.post("/api/rules/infer").json()["error"] == "no_sample"


def test_validate_rules(client):
    assert client.post("/api/rules/validate", json=orders_rules()).json() == {"ok": True, "errors": []}
    bad = client.post("/api/rules/validate", json={"fields": {"a": {"requird": True}}}).json()
    assert bad["ok"] is False and "requird" in bad["errors"][0]
    not_json = client.post("/api/rules/validate", content=b"{oops",
                           headers={"Content-Type": "application/json"}).json()
    assert not_json["ok"] is False


def test_check_with_invalid_rules(client):
    dataset_id = upload(client).json()["dataset_id"]
    response = client.post("/api/check", json={"dataset_id": dataset_id,
                                               "ruleset": {"fields": {}}})
    assert response.status_code == 400
    assert response.json()["errors"]


def test_check_with_unknown_dataset(client):
    response = client.post("/api/check", json={"dataset_id": "nope", "ruleset": orders_rules()})
    assert response.status_code == 404


def test_only_5_datasets_are_kept(client):
    ids = [upload(client).json()["dataset_id"] for _ in range(6)]
    first = client.post("/api/check", json={"dataset_id": ids[0], "ruleset": orders_rules()})
    last = client.post("/api/check", json={"dataset_id": ids[-1], "ruleset": orders_rules()})
    assert first.status_code == 404
    assert last.status_code == 200


def test_datasets_expire(client, monkeypatch):
    dataset_id = upload(client).json()["dataset_id"]
    monkeypatch.setattr(settings, "DATASET_EXPIRY_SECONDS", -1)
    response = client.post("/api/check", json={"dataset_id": dataset_id, "ruleset": orders_rules()})
    assert response.status_code == 404


# ---------- REST fetch through the server, against its own demo API ----------

@pytest.fixture
def fetch_via_demo(client, monkeypatch):
    """Make the server's fetch talk to the demo endpoints of the same test app."""
    real_fetch = fetch_module.fetch_dataset
    demo_api = TestClient(client.app)

    # fetch uses httpx, but TestClient uses httpx2 when it is installed (it comes
    # with langgraph). Their transports don't mix, so pass requests across by hand.
    def forward(request: httpx.Request) -> httpx.Response:
        response = demo_api.request(request.method, str(request.url),
                                    headers=dict(request.headers), content=request.content,
                                    follow_redirects=False)
        return httpx.Response(response.status_code, headers=list(response.headers.items()),
                              content=response.content)

    def fake_fetch(url, data_path="", token=None, transport=None):
        return real_fetch(url, data_path, token, transport=httpx.MockTransport(forward))

    monkeypatch.setattr("probagent.dq.server.fetch_dataset", fake_fetch)

    def call(path, token=None):
        return client.post("/api/data/fetch", json={"url": "http://testserver" + path,
                                                    "data_path": "data", "token": token})
    return call


def test_fetch_open_demo_api(fetch_via_demo):
    response = fetch_via_demo("/demo/api/orders")
    assert response.status_code == 200
    assert response.json()["row_count"] == 201


def test_fetch_secure_demo_api(fetch_via_demo):
    first = fetch_via_demo("/demo/api/secure-orders")
    assert first.json()["error"] == "auth_required"

    wrong = fetch_via_demo("/demo/api/secure-orders", token="wrong.token.here")
    assert wrong.json()["error"] == "auth_failed"
    assert "wrong.token.here" not in wrong.text

    right = fetch_via_demo("/demo/api/secure-orders", token=DEMO_TOKEN)
    assert right.status_code == 200
    assert DEMO_TOKEN not in right.text


def test_fetch_404_does_not_ask_for_token(fetch_via_demo):
    response = fetch_via_demo("/no/such/api")
    assert response.json()["error"] == "fetch_failed"
    assert response.json()["status"] == 404


def test_demo_endpoints_are_off_by_default():
    assert TestClient(create_app()).get("/demo/api/orders").status_code == 404
