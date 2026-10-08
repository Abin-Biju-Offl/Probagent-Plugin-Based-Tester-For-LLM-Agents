"""Fetching data from a REST API, with a JWT only if needed (spec Section 5.2).

    dataset = fetch_dataset("https://api.example.com/orders", data_path="data")

The flow:
  1. GET the URL with no login.
  2. 401 or 403?  -> FetchError("auth_required"). The page then asks for a JWT.
  3. GET again with "Authorization: Bearer <token>".
     Still 401/403? -> FetchError("auth_failed").
  4. Any other problem (404, 500, timeout, not JSON) -> FetchError("fetch_failed").
     A token would not fix these, so the page does not ask for one.

The token is only used inside this one call. It is never stored, logged,
or put in an error message.
"""

import hashlib
import json
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from probagent.dq import settings
from probagent.dq.loader import MB, LoadError, records_to_dataset
from probagent.dq.models import Dataset

AUTH_STATUSES = (401, 403)


class FetchError(Exception):
    """Fetching failed.

    code:   "auth_required" | "auth_failed" | "fetch_failed"
    status: the HTTP status from the API, if there was one
    """

    def __init__(self, code: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def fetch_dataset(url: str, data_path: str = "", token: Optional[str] = None,
                  transport: Optional[httpx.BaseTransport] = None) -> Dataset:
    """Download JSON from `url` and turn it into a Dataset.

    `transport` is only used by tests, to fake the API.
    """
    check_url(url)
    token = (token or "").strip() or None

    status, body = get_url(url, token, transport)

    if status in AUTH_STATUSES:
        if token is None:
            raise FetchError("auth_required", "This API needs a login. Paste a JWT token "
                             "and try again.", status)
        raise FetchError("auth_failed", "The token was rejected. Check it is valid and "
                         "not expired.", status)
    if not 200 <= status < 300:
        raise FetchError("fetch_failed", f"The API answered with HTTP {status} "
                         f"({httpx.codes.get_reason_phrase(status) or 'error'}).", status)

    try:
        data = json.loads(body)
    except ValueError:
        raise FetchError("fetch_failed", "The API response is not valid JSON.", status) from None

    records = follow_data_path(data, data_path)
    try:
        dataset = records_to_dataset(records, source=safe_url(url))
    except LoadError as error:
        raise FetchError("fetch_failed", str(error), status) from None
    dataset.sha256 = hashlib.sha256(body).hexdigest()
    return dataset


def check_url(url: str) -> None:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FetchError("fetch_failed", "The URL must start with http:// or https://.")


def get_url(url: str, token: Optional[str],
            transport: Optional[httpx.BaseTransport]) -> tuple[int, bytes]:
    """GET the URL and return (status code, body bytes).

    We follow redirects ourselves (at most 3) so we can make sure the token is
    only sent to the same server the user typed in, never to another one.
    """
    original_origin = origin_of(url)
    current_url = url

    try:
        with httpx.Client(transport=transport, timeout=settings.FETCH_TIMEOUT_SECONDS,
                          follow_redirects=False) as client:
            for _ in range(settings.MAX_REDIRECTS + 1):
                headers = {"Accept": "application/json"}
                request_url = current_url
                if token and origin_of(current_url) == original_origin:
                    headers["Authorization"] = f"Bearer {token}"
                    # httpx turns "user:password@" in a URL into a Basic login that
                    # would replace our header. When a token is given, the token wins.
                    request_url = without_login(current_url)

                with client.stream("GET", request_url, headers=headers) as response:
                    if response.is_redirect:
                        current_url = urljoin(current_url, response.headers["location"])
                        continue
                    return response.status_code, read_limited(response)

    except httpx.TimeoutException:
        raise FetchError("fetch_failed", f"The API did not answer within "
                         f"{settings.FETCH_TIMEOUT_SECONDS} seconds (timeout).") from None
    except httpx.HTTPError as error:
        raise FetchError("fetch_failed", f"Could not connect to the API "
                         f"({type(error).__name__}).") from None

    raise FetchError("fetch_failed", f"The API redirected more than "
                     f"{settings.MAX_REDIRECTS} times.")


def safe_url(url: str) -> str:
    """The URL without a password, query string or fragment.

    API keys often hide in "?api_key=...", so this is the version we show
    in reports and write to the run log.
    """
    parts = urlsplit(without_login(url.strip()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def without_login(url: str) -> str:
    """Remove "user:password@" from a URL, keeping everything else."""
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def origin_of(url: str) -> tuple[str, str]:
    """('https', 'api.example.com:443')-style key: scheme plus host and port."""
    parts = urlsplit(url)
    return parts.scheme, parts.netloc.lower()


def read_limited(response: httpx.Response) -> bytes:
    """Read the body, but stop as soon as it passes the size limit."""
    body = b""
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > settings.MAX_FILE_BYTES:
            raise FetchError("fetch_failed", f"The API response is larger than the "
                             f"{settings.MAX_FILE_BYTES // MB} MB demo limit.",
                             response.status_code)
    return body


def follow_data_path(data: Any, data_path: str) -> Any:
    """Walk into the JSON: data_path "data.items" means data["data"]["items"]."""
    if not data_path.strip():
        return data
    for key in data_path.strip().split("."):
        if not isinstance(data, dict) or key not in data:
            raise FetchError("fetch_failed", f'The data path "{data_path}" was not found '
                             f'in the response (stopped at "{key}").')
        data = data[key]
    return data
