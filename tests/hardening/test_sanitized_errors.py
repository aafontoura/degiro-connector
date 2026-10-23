"""H-2: failures are typed codes with no URL, body, token or credential."""

from datetime import date

import pytest
import requests

from degiro_connector.hardened import DegiroError, ErrorCode

from .conftest import (
    BODY_MARKER,
    SESSION_ID,
    TOKEN_MARKER,
    assert_sanitized,
    raise_,
    reply,
)

URL_WITH_SESSION = f"https://trader.degiro.nl/pa/secure/client?sessionId={SESSION_ID}"


@pytest.mark.parametrize(
    ("answer", "code", "status"),
    [
        (reply(400, {"status": 3, "error": BODY_MARKER}), ErrorCode.LOGIN_REJECTED, 400),
        (reply(400, {"status": 6, "error": BODY_MARKER}), ErrorCode.OTP_REQUIRED, 400),
        (
            reply(400, {"status": 12, "inAppToken": TOKEN_MARKER}),
            ErrorCode.IN_APP_APPROVAL_REQUIRED,
            400,
        ),
        (
            reply(400, {"status": 3, "captchaRequired": True, "error": BODY_MARKER}),
            ErrorCode.CAPTCHA_REQUIRED,
            400,
        ),
        (reply(405, BODY_MARKER), ErrorCode.MAINTENANCE, 405),
        (reply(500, f"<html>{BODY_MARKER}</html>"), ErrorCode.LOGIN_REJECTED, 500),
        (reply(200, BODY_MARKER), ErrorCode.SCHEMA, None),
        (reply(200, {"status": 0, "note": BODY_MARKER}), ErrorCode.SCHEMA, None),
        (raise_(requests.ConnectTimeout(URL_WITH_SESSION)), ErrorCode.TIMEOUT, None),
        (raise_(requests.ConnectionError(URL_WITH_SESSION)), ErrorCode.TRANSPORT, None),
        (raise_(RuntimeError(URL_WITH_SESSION)), ErrorCode.TRANSPORT, None),
    ],
)
def test_login_failures(client, adapter, credentials, answer, code, status):
    adapter.queue(answer)
    with pytest.raises(DegiroError) as raised:
        client.login(credentials)
    assert raised.value.code is code
    assert raised.value.status == status
    assert_sanitized(raised.value)


def test_maintenance_makes_no_second_request(client, adapter, credentials):
    adapter.queue(reply(405, BODY_MARKER))
    with pytest.raises(DegiroError):
        client.login(credentials)
    assert len(adapter.sent) == 1


@pytest.mark.parametrize(
    ("answer", "code", "status"),
    [
        (reply(401, BODY_MARKER), ErrorCode.UNAUTHORIZED, 401),
        (reply(403, BODY_MARKER), ErrorCode.UNAUTHORIZED, 403),
        (reply(429, BODY_MARKER), ErrorCode.RATE_LIMITED, 429),
        (reply(503, BODY_MARKER), ErrorCode.SERVER_ERROR, 503),
        (reply(404, BODY_MARKER), ErrorCode.HTTP_STATUS, 404),
        (reply(200, f"not json {BODY_MARKER}"), ErrorCode.SCHEMA, None),
        (raise_(requests.ReadTimeout(URL_WITH_SESSION)), ErrorCode.TIMEOUT, None),
        (
            raise_(requests.HTTPError(f"500 Server Error for url: {URL_WITH_SESSION}")),
            ErrorCode.TRANSPORT,
            None,
        ),
    ],
)
def test_read_failures(connected, adapter, answer, code, status):
    adapter.queue(answer)
    with pytest.raises(DegiroError) as raised:
        connected.get_account_overview(date(2026, 1, 1), date(2026, 1, 31))
    assert raised.value.code is code
    assert raised.value.status == status
    assert_sanitized(raised.value)


def test_client_details_without_an_account_is_a_schema_error(client, adapter, credentials):
    from .conftest import LOGIN_OK

    adapter.queue(LOGIN_OK, reply(200, {"data": {"intAccount": True, "x": BODY_MARKER}}))
    client.login(credentials)
    with pytest.raises(DegiroError) as raised:
        client.get_client_details()
    assert raised.value.code is ErrorCode.SCHEMA
    assert_sanitized(raised.value)


@pytest.mark.parametrize("session_id", ["a?b=c", "a/b", "a#b", "a;b", "x" * 129, ""])
def test_a_session_id_that_could_reshape_a_url_is_refused(client, adapter, credentials, session_id):
    adapter.queue(reply(200, {"sessionId": session_id}))
    with pytest.raises(DegiroError) as raised:
        client.login(credentials)
    assert raised.value.code is ErrorCode.SCHEMA


def test_errors_survive_a_process_hop():
    import pickle

    error = pickle.loads(pickle.dumps(DegiroError(ErrorCode.RATE_LIMITED, 429)))
    assert error.code is ErrorCode.RATE_LIMITED
    assert error.status == 429


def test_upstream_login_errors_do_not_show_tokens_or_server_text():
    from degiro_connector.trading.models.login import LoginError, LoginSuccess

    error = LoginError(status=12, error=BODY_MARKER, in_app_token=TOKEN_MARKER)
    success = LoginSuccess(session_id=SESSION_ID)
    for text in (repr(error), str(error), repr(success), repr(ConnectionError("x", error))):
        assert BODY_MARKER not in text
        assert TOKEN_MARKER not in text
        assert SESSION_ID not in text
    assert error.in_app_token == TOKEN_MARKER  # still usable by the in-app flow


def test_status_is_only_ever_an_int():
    assert DegiroError(ErrorCode.TRANSPORT, status=BODY_MARKER).status is None  # type: ignore[arg-type]
    assert DegiroError(ErrorCode.TRANSPORT, status=True).status is None
