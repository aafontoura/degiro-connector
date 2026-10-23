"""Fixtures for the hardening tests.

No test here touches the network: a fake ``requests`` adapter records every
prepared request and answers from a script. All values are synthetic markers
chosen so that a leak is a plain substring match.
"""

import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from degiro_connector.hardened import OtpCredentials, ReadOnlyTradingClient
from degiro_connector.hardened.transport import Transport

USERNAME = "synthetic-user"
PASSWORD = "PASSWORD-MARKER-7f3a"
OTP = "024680"
SESSION_ID = "SESSION-MARKER-9c1e"
INT_ACCOUNT = 31415926
BODY_MARKER = "BODY-MARKER-55aa"
TOKEN_MARKER = "TOKEN-MARKER-0b0b"

SECRET_MARKERS = (PASSWORD, OTP, SESSION_ID, BODY_MARKER, TOKEN_MARKER)


def make_response(
    request: requests.PreparedRequest,
    status: int = 200,
    body: str | bytes = "",
    headers: dict[str, str] | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    raw = body.encode("utf-8") if isinstance(body, str) else body
    response.raw = io.BytesIO(raw)
    response.headers = CaseInsensitiveDict(headers or {})
    response.url = request.url or ""
    response.request = request
    return response


Reply = Callable[[requests.PreparedRequest], requests.Response]


def reply(status: int = 200, body: Any = None, headers: dict[str, str] | None = None) -> Reply:
    text = body if isinstance(body, (str, bytes)) else json.dumps(body if body is not None else {})
    return lambda request: make_response(request, status, text, headers)


def raise_(exc: Exception) -> Reply:
    def _raise(request: requests.PreparedRequest) -> requests.Response:
        raise exc

    return _raise


@dataclass
class Sent:
    request: requests.PreparedRequest
    kwargs: dict[str, Any]

    @property
    def json(self) -> Any:
        body = self.request.body
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return json.loads(body) if body else None


@dataclass
class FakeAdapter(BaseAdapter):
    replies: list[Reply] = field(default_factory=list)
    sent: list[Sent] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        super().__init__()

    def queue(self, *replies: Reply) -> "FakeAdapter":
        self.replies.extend(replies)
        return self

    def send(self, request, **kwargs):  # type: ignore[override]
        self.sent.append(Sent(request=request, kwargs=kwargs))
        if not self.replies:
            raise AssertionError("unexpected request")
        return self.replies.pop(0)(request)

    def close(self) -> None:
        self.closed = True


LOGIN_OK = reply(200, {"sessionId": SESSION_ID, "status": 0, "statusText": "success"})
CLIENT_DETAILS_OK = reply(
    200, {"data": {"intAccount": INT_ACCOUNT, "clientId": 1, "username": USERNAME}}
)
LOGOUT_OK = reply(200, "")


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
def transport(adapter: FakeAdapter) -> Transport:
    return Transport(adapter=adapter)


@pytest.fixture
def client(transport: Transport) -> ReadOnlyTradingClient:
    return ReadOnlyTradingClient(transport=transport)


@pytest.fixture
def credentials() -> OtpCredentials:
    return OtpCredentials(USERNAME, PASSWORD, OTP)


@pytest.fixture
def connected(
    client: ReadOnlyTradingClient, adapter: FakeAdapter, credentials: OtpCredentials
) -> ReadOnlyTradingClient:
    adapter.queue(LOGIN_OK, CLIENT_DETAILS_OK)
    client.login(credentials)
    client.get_client_details()
    return client


def assert_clean(text: str) -> None:
    for marker in SECRET_MARKERS:
        assert marker not in text, f"leaked {marker!r}"


def assert_sanitized(error: BaseException) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert_clean(str(error))
    assert_clean(repr(error))
    assert_clean(repr(error.args))
