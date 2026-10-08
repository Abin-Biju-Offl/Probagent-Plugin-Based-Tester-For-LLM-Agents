"""REST fetch tests. httpx.MockTransport plays the role of the API."""

import logging

import httpx
import pytest

from probagent.dq import settings
from probagent.dq.fetch import FetchError, fetch_dataset

TOKEN = "header.payload.secret-signature"
RECORDS = {"data": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}


def secure_api(request: httpx.Request) -> httpx.Response:
    """Answers 401 unless the right token is sent."""
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        return httpx.Response(401, json={"detail": "Not authenticated"})
    return httpx.Response(200, json=RECORDS)


def fetch(handler, url="https://api.example.com/orders", **kwargs):
    return fetch_dataset(url, transport=httpx.MockTransport(handler), **kwargs)


def fetch_error(handler, **kwargs) -> FetchError:
    with pytest.raises(FetchError) as caught:
        fetch(handler, **kwargs)
    return caught.value


# ---------- Phase 4 acceptance ----------

def test_open_api_loads_directly():
    dataset = fetch(lambda request: httpx.Response(200, json=RECORDS), data_path="data")
    assert dataset.columns == ["id", "name"]
    assert len(dataset.rows) == 2
    assert dataset.source == "https://api.example.com/orders"


def test_first_request_has_no_token():
    sent = []
    fetch(lambda request: sent.append(request.headers) or httpx.Response(200, json=[{"a": 1}]))
    assert "authorization" not in sent[0]


def test_secure_api_asks_for_token_then_works_with_it():
    assert fetch_error(secure_api, data_path="data").code == "auth_required"
    dataset = fetch(secure_api, data_path="data", token=TOKEN)
    assert len(dataset.rows) == 2


def test_wrong_token_is_auth_failed():
    error = fetch_error(secure_api, token="wrong.token.value")
    assert error.code == "auth_failed"
    assert error.status == 401


def test_403_also_asks_for_token():
    assert fetch_error(lambda r: httpx.Response(403)).code == "auth_required"


@pytest.mark.parametrize("status", [404, 500, 503])
def test_other_http_errors_do_not_ask_for_token(status):
    error = fetch_error(lambda r: httpx.Response(status))
    assert error.code == "fetch_failed"
    assert str(status) in error.message


def test_timeout_is_fetch_failed():
    def slow_api(request):
        raise httpx.ReadTimeout("too slow", request=request)
    error = fetch_error(slow_api)
    assert error.code == "fetch_failed" and "timeout" in error.message


def test_connection_error_is_fetch_failed():
    def down_api(request):
        raise httpx.ConnectError("refused", request=request)
    assert fetch_error(down_api).code == "fetch_failed"


def test_token_never_appears_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    for handler in (secure_api, lambda r: httpx.Response(500)):
        error = fetch_error(handler, token="wrong.token.value")
        assert "wrong.token.value" not in str(error) + error.message
    assert "wrong.token.value" not in caplog.text


def test_token_is_not_sent_to_another_host_after_redirect():
    seen = {}

    def api(request: httpx.Request):
        seen[request.url.host] = request.headers.get("authorization")
        if request.url.host == "api.example.com":
            return httpx.Response(302, headers={"location": "https://other.example.net/data"})
        return httpx.Response(200, json=[{"a": 1}])

    fetch(api, token=TOKEN)
    assert seen["api.example.com"] == f"Bearer {TOKEN}"
    assert seen["other.example.net"] is None


def test_too_many_redirects():
    loop = lambda r: httpx.Response(302, headers={"location": "/again"})
    assert "redirected more than" in fetch_error(loop).message


# ---------- Turning the response into a table ----------

def test_not_json():
    error = fetch_error(lambda r: httpx.Response(200, text="<html>hello</html>"))
    assert "not valid JSON" in error.message


def test_data_path_not_found():
    error = fetch_error(lambda r: httpx.Response(200, json=RECORDS), data_path="data.items")
    assert 'stopped at "items"' in error.message


def test_response_must_be_list_of_records():
    error = fetch_error(lambda r: httpx.Response(200, json=RECORDS))   # forgot data path
    assert "not a list of records" in error.message


def test_response_size_limit(monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_BYTES", 50)
    error = fetch_error(lambda r: httpx.Response(200, json=[{"a": "x" * 100}]))
    assert "larger than" in error.message


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "example.com"])
def test_only_http_urls(url):
    with pytest.raises(FetchError, match="http:// or https://"):
        fetch_dataset(url)
