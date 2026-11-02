"""H-4, H-5, H-6: finite timeouts, no redirects or ambient proxy, one host."""

from datetime import date

import pytest

from degiro_connector.core.models import model_session
from degiro_connector.core.models.model_session import ModelSession
from degiro_connector.hardened import DegiroError, ErrorCode
from degiro_connector.hardened.transport import (
    DEFAULT_BUDGET,
    LOGOUT_BUDGET,
    Transport,
)
from degiro_connector.trading.actions import action_connect, action_logout
from degiro_connector.trading.actions.action_connect import ActionConnect
from degiro_connector.trading.actions.action_logout import ActionLogout
from degiro_connector.trading.actions.action_get_account_overview import (
    ActionGetAccountOverview,
)
from degiro_connector.trading.models.account import OverviewRequest
from degiro_connector.trading.models.credentials import Credentials

from .conftest import (
    CLIENT_DETAILS_OK,
    INT_ACCOUNT,
    LOGIN_OK,
    LOGOUT_OK,
    OTP,
    PASSWORD,
    SESSION_ID,
    USERNAME,
    FakeAdapter,
    reply,
)

CLIENT_URL = "https://trader.degiro.nl/pa/secure/client"


def _finite(timeout) -> bool:
    return (
        isinstance(timeout, tuple)
        and len(timeout) == 2
        and all(isinstance(t, (int, float)) and 0 < t < 60 for t in timeout)
    )


def test_every_request_has_a_finite_timeout(client, adapter, credentials):
    adapter.queue(
        LOGIN_OK,
        CLIENT_DETAILS_OK,
        reply(200, {"data": {"baseCurrency": "EUR"}}),
        reply(200, {"data": []}),
        reply(200, {"data": {"cashMovements": []}}),
        reply(200, {"portfolio": {}}),
        reply(200, {"data": {}}),
        LOGOUT_OK,
    )
    client.login(credentials)
    client.get_client_details()
    client.get_account_info()
    client.get_transactions_history(date(2026, 1, 1), date(2026, 1, 31))
    client.get_account_overview(date(2026, 1, 1), date(2026, 1, 31))
    client.get_update(["portfolio", "cashFunds"])
    client.get_products_info([1, 2])
    client.close()

    assert len(adapter.sent) == 8
    assert all(_finite(sent.kwargs["timeout"]) for sent in adapter.sent)
    assert adapter.sent[0].kwargs["timeout"] == (DEFAULT_BUDGET.connect, DEFAULT_BUDGET.read)
    assert adapter.sent[-1].kwargs["timeout"] == (LOGOUT_BUDGET.connect, LOGOUT_BUDGET.read)


def test_ambient_proxy_environment_is_ignored(monkeypatch, client, adapter, credentials):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/nonexistent/ca.pem")
    adapter.queue(LOGIN_OK)
    client.login(credentials)
    sent = adapter.sent[0]
    assert not any(sent.kwargs["proxies"].values())
    assert sent.kwargs["verify"] is True


def test_session_does_not_trust_the_environment(transport):
    assert transport.session.trust_env is False
    assert transport.session.proxies == {}
    assert "http://" not in transport.session.adapters


@pytest.mark.parametrize(
    "location",
    ["https://evil.example/collect", "https://trader.degiro.nl/elsewhere", "/relative"],
)
def test_any_redirect_is_refused_and_not_followed(client, adapter, credentials, location):
    adapter.queue(reply(302, "", headers={"Location": location}))
    with pytest.raises(DegiroError) as raised:
        client.login(credentials)
    assert raised.value.code is ErrorCode.REDIRECT_BLOCKED
    assert len(adapter.sent) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/pa/secure/client",
        "http://trader.degiro.nl/pa/secure/client",
        "https://trader.degiro.nl:8443/pa/secure/client",
        "https://user@trader.degiro.nl/pa/secure/client",
        "https://trader.degiro.nl.evil.example/pa/secure/client",
        "https://charting.vwdservices.com/pa/secure/client",
        "https://trader.degiro.nl/pa/secure/client#x",
    ],
)
def test_only_trader_degiro_nl_443_is_reachable(transport, adapter, url):
    with pytest.raises(DegiroError) as raised:
        transport.send("client_details", url)
    assert raised.value.code is ErrorCode.HOST_NOT_ALLOWED
    assert adapter.sent == []


def test_explicit_port_443_is_allowed(transport, adapter):
    adapter.queue(reply(200, {}))
    transport.send("client_details", "https://trader.degiro.nl:443/pa/secure/client")
    assert len(adapter.sent) == 1


def test_response_size_is_bounded(adapter):
    transport = Transport(adapter=adapter, max_response_bytes=16)
    adapter.queue(reply(200, "x" * 17))
    with pytest.raises(DegiroError) as raised:
        transport.send("client_details", CLIENT_URL)
    assert raised.value.code is ErrorCode.RESPONSE_TOO_LARGE


def test_upstream_session_does_not_trust_the_environment():
    assert ModelSession.build_session().trust_env is False


def test_upstream_login_and_maintenance_probe_use_the_session_with_timeouts():
    adapter = FakeAdapter().queue(
        reply(405, ""),
        reply(200, "<div id='under-const-content'><p>maintenance</p></div>"),
    )
    session = ModelSession.build_session()
    session.mount("https://", adapter)
    credentials = Credentials(username=USERNAME, password=PASSWORD, one_time_password=OTP)
    with pytest.raises(ConnectionError):
        ActionConnect.get_session_id(credentials=credentials, session=session)

    assert len(adapter.sent) == 2
    probe = adapter.sent[1]
    assert probe.request.url == "https://trader.degiro.nl/login"
    assert probe.kwargs["timeout"] == action_connect.LOGIN_TIMEOUT
    assert not any(probe.kwargs["proxies"].values())


def test_upstream_reads_without_a_timeout_get_the_default():
    adapter = FakeAdapter().queue(reply(200, {"data": {"cashMovements": []}}))
    session = ModelSession.build_session()
    session.mount("https://", adapter)
    credentials = Credentials(int_account=INT_ACCOUNT, username=USERNAME, password=PASSWORD)
    ActionGetAccountOverview.get_account_overview(
        overview_request=OverviewRequest(from_date=date(2026, 1, 1), to_date=date(2026, 1, 2)),
        session_id=SESSION_ID,
        credentials=credentials,
        session=session,
    )
    assert adapter.sent[0].kwargs["timeout"] == model_session.DEFAULT_TIMEOUT


def test_upstream_logout_has_a_timeout():
    adapter = FakeAdapter().queue(LOGOUT_OK)
    session = ModelSession.build_session()
    session.mount("https://", adapter)
    credentials = Credentials(int_account=INT_ACCOUNT, username=USERNAME, password=PASSWORD)
    ActionLogout.logout(credentials=credentials, session_id=SESSION_ID, session=session)
    assert adapter.sent[0].kwargs["timeout"] == action_logout.LOGOUT_TIMEOUT
