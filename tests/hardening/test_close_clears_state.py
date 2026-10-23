"""H-10: close() logs out within a budget and always clears session state."""

from datetime import date

import pytest
import requests

from degiro_connector.hardened import DegiroError, ErrorCode, OtpCredentials

from .conftest import (
    INT_ACCOUNT,
    LOGIN_OK,
    LOGOUT_OK,
    OTP,
    PASSWORD,
    SESSION_ID,
    USERNAME,
    raise_,
    reply,
)


def _assert_forgotten(client, transport, adapter):
    assert client._session_id is None
    assert client._int_account is None
    assert transport.session is None
    assert adapter.closed is True


def test_close_logs_out_and_forgets(connected, transport, adapter):
    session = transport.session
    session.cookies.set("JSESSIONID", SESSION_ID, domain="trader.degiro.nl")
    adapter.queue(LOGOUT_OK)

    assert connected.close() is True

    logout = adapter.sent[-1]
    assert logout.request.method == "PUT"
    assert f";jsessionid={SESSION_ID}" in logout.request.url
    assert f"intAccount={INT_ACCOUNT}" in logout.request.url
    assert len(session.cookies) == 0
    _assert_forgotten(connected, transport, adapter)


@pytest.mark.parametrize(
    "failure",
    [
        raise_(requests.ReadTimeout("logout hung")),
        raise_(requests.ConnectionError("gone")),
        reply(500, "boom"),
        reply(302, "", headers={"Location": "https://evil.example/"}),
    ],
)
def test_failed_logout_still_forgets(connected, transport, adapter, failure):
    adapter.queue(failure)
    assert connected.close() is False
    _assert_forgotten(connected, transport, adapter)


def test_close_without_a_session_sends_nothing(client, transport, adapter):
    assert client.close() is None
    assert adapter.sent == []
    assert transport.session is None


def test_closed_client_refuses_everything(connected, adapter):
    adapter.queue(LOGOUT_OK)
    connected.close()
    sent = len(adapter.sent)
    for call in (
        connected.get_client_details,
        lambda: connected.get_transactions_history(date(2026, 1, 1), date(2026, 1, 2)),
        lambda: connected.login(OtpCredentials(USERNAME, PASSWORD, OTP)),
    ):
        with pytest.raises(DegiroError) as raised:
            call()
        assert raised.value.code is ErrorCode.CLOSED
    assert connected.close() is None
    assert len(adapter.sent) == sent


def test_context_manager_closes(client, transport, adapter, credentials):
    adapter.queue(LOGIN_OK, LOGOUT_OK)
    with client:
        client.login(credentials)
    assert transport.session is None
    assert adapter.sent[-1].request.method == "PUT"


def test_credentials_are_spent_by_login_even_when_it_fails(client, adapter, credentials):
    adapter.queue(raise_(requests.ConnectTimeout("no route")))
    with pytest.raises(DegiroError):
        client.login(credentials)
    assert credentials.is_spent
    with pytest.raises(DegiroError):
        credentials.take()


def test_login_on_a_closed_client_spends_its_credentials(client):
    client.close()
    late = OtpCredentials(USERNAME, PASSWORD, OTP)
    with pytest.raises(DegiroError) as raised:
        client.login(late)
    assert raised.value.code is ErrorCode.CLOSED
    assert late.is_spent


def test_a_second_login_is_refused_and_spends_its_credentials(connected):
    second = OtpCredentials(USERNAME, PASSWORD, OTP)
    with pytest.raises(DegiroError) as raised:
        connected.login(second)
    assert raised.value.code is ErrorCode.ALREADY_CONNECTED
    assert second.is_spent


def test_reads_need_a_session(client):
    with pytest.raises(DegiroError) as raised:
        client.get_client_details()
    assert raised.value.code is ErrorCode.NOT_CONNECTED


def test_credentials_never_show_their_values():
    credentials = OtpCredentials(USERNAME, PASSWORD, OTP)
    for text in (repr(credentials), str(credentials)):
        assert PASSWORD not in text and OTP not in text
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(credentials)
