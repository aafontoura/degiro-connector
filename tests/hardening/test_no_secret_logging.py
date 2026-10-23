"""H-1: no login, response or session logging."""

import logging
from datetime import date

import pytest
import requests

from degiro_connector.core.models.model_session import ModelSession
from degiro_connector.hardened import DegiroError, OtpCredentials
from degiro_connector.trading.actions.action_connect import ActionConnect
from degiro_connector.trading.actions.action_get_transactions_history import (
    ActionGetTransactionsHistory,
)
from degiro_connector.trading.actions.action_logout import ActionLogout
from degiro_connector.trading.models.credentials import Credentials
from degiro_connector.trading.models.transaction import HistoryRequest

from .conftest import (
    BODY_MARKER,
    CLIENT_DETAILS_OK,
    INT_ACCOUNT,
    LOGIN_OK,
    LOGOUT_OK,
    OTP,
    PASSWORD,
    SESSION_ID,
    TOKEN_MARKER,
    USERNAME,
    FakeAdapter,
    assert_clean,
    raise_,
    reply,
)

URL_WITH_SESSION = (
    "https://trader.degiro.nl/portfolio-reports/secure/v4/transactions"
    f"?sessionId={SESSION_ID}"
)


@pytest.fixture(autouse=True)
def capture_everything(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def test_hardened_success_path_logs_nothing(client, adapter, credentials, caplog):
    history = {"data": [{"id": 1, "price": 1.5, "note": BODY_MARKER}]}
    adapter.queue(LOGIN_OK, CLIENT_DETAILS_OK, reply(200, history), LOGOUT_OK)

    client.login(credentials)
    client.get_client_details()
    client.get_transactions_history(date(2026, 1, 1), date(2026, 1, 31))
    client.close()

    assert [r for r in caplog.records if r.name.startswith("degiro_connector")] == []
    assert_clean(caplog.text)


@pytest.mark.parametrize(
    "failure",
    [
        reply(500, BODY_MARKER),
        reply(401, {"error": BODY_MARKER}),
        raise_(requests.ConnectionError(f"failed for url: {URL_WITH_SESSION}")),
        raise_(requests.ReadTimeout(f"timed out: {URL_WITH_SESSION}")),
    ],
)
def test_hardened_failure_paths_log_nothing(connected, adapter, caplog, failure):
    adapter.queue(failure)
    with pytest.raises(DegiroError):
        connected.get_transactions_history(date(2026, 1, 1), date(2026, 1, 31))
    assert_clean(caplog.text)


def test_hardened_login_rejection_logs_nothing(client, adapter, caplog):
    adapter.queue(reply(400, {"status": 3, "error": BODY_MARKER, "inAppToken": TOKEN_MARKER}))
    with pytest.raises(DegiroError):
        client.login(OtpCredentials(USERNAME, PASSWORD, OTP))
    assert_clean(caplog.text)


def _upstream_session(adapter: FakeAdapter) -> requests.Session:
    session = ModelSession.build_session()
    session.mount("https://", adapter)
    return session


def test_upstream_login_success_no_longer_logs_the_session(caplog):
    adapter = FakeAdapter().queue(LOGIN_OK)
    credentials = Credentials(username=USERNAME, password=PASSWORD, one_time_password=OTP)
    ActionConnect.get_session_id(credentials=credentials, session=_upstream_session(adapter))
    assert_clean(caplog.text)


def test_upstream_login_error_logs_status_only(caplog):
    adapter = FakeAdapter().queue(
        reply(400, {"status": 3, "error": BODY_MARKER, "inAppToken": TOKEN_MARKER})
    )
    credentials = Credentials(username=USERNAME, password=PASSWORD, one_time_password=OTP)
    with pytest.raises(ConnectionError):
        ActionConnect.get_session_id(credentials=credentials, session=_upstream_session(adapter))
    assert_clean(caplog.text)


def test_upstream_history_error_logs_neither_body_nor_url(caplog):
    adapter = FakeAdapter().queue(reply(500, BODY_MARKER))
    credentials = Credentials(int_account=INT_ACCOUNT, username=USERNAME, password=PASSWORD)
    result = ActionGetTransactionsHistory.get_transactions_history(
        transaction_request=HistoryRequest(from_date=date(2026, 1, 1), to_date=date(2026, 1, 2)),
        session_id=SESSION_ID,
        credentials=credentials,
        session=_upstream_session(adapter),
    )
    assert result is None
    assert_clean(caplog.text)


def test_upstream_logout_error_logs_neither_body_nor_url(caplog):
    adapter = FakeAdapter().queue(reply(500, BODY_MARKER))
    credentials = Credentials(int_account=INT_ACCOUNT, username=USERNAME, password=PASSWORD)
    result = ActionLogout.logout(
        credentials=credentials,
        session_id=SESSION_ID,
        session=_upstream_session(adapter),
    )
    assert result is None
    assert_clean(caplog.text)
