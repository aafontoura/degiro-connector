"""H-7, H-9: an explicit read-only surface; no order, dynamic or credential-file path."""

import ast
import inspect
from pathlib import Path

import pytest

import degiro_connector.hardened as hardened
from degiro_connector.core.constants import urls
from degiro_connector.hardened import DegiroError, ErrorCode, ReadOnlyTradingClient
from degiro_connector.hardened.transport import ALLOWED_OPERATIONS
from degiro_connector.trading import actions
from degiro_connector.trading.models import credentials as upstream_credentials

from .conftest import INT_ACCOUNT, LOGIN_OK, SESSION_ID, reply

HARDENED_DIR = Path(hardened.__file__).parent
ACTIONS_DIR = Path(actions.__file__).parent

EXPECTED_METHODS = {
    "login",
    "close",
    "get_client_details",
    "get_account_info",
    "get_transactions_history",
    "get_account_overview",
    "get_update",
    "get_products_info",
}


def _public_callables(cls) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls)
        if not name.startswith("_") and callable(member)
    }


def test_client_exposes_exactly_the_read_operations():
    assert _public_callables(ReadOnlyTradingClient) == EXPECTED_METHODS
    assert "__getattr__" not in vars(ReadOnlyTradingClient)


def test_transport_allowlist_is_read_only():
    assert set(ALLOWED_OPERATIONS) == {
        "login",
        "client_details",
        "account_info",
        "transactions_history",
        "account_overview",
        "update",
        "products_info",
        "logout",
    }
    methods = {name: method for name, (method, _) in ALLOWED_OPERATIONS.items()}
    assert {n for n, m in methods.items() if m != "GET"} == {"login", "products_info", "logout"}
    for _, pattern in ALLOWED_OPERATIONS.values():
        assert "order" not in pattern.pattern.lower()


@pytest.mark.parametrize(
    ("operation", "url"),
    [
        ("check_order", "https://trader.degiro.nl/trading/secure/v5/checkOrder"),
        ("client_details", "https://trader.degiro.nl/trading/secure/v5/checkOrder"),
        ("client_details", "https://trader.degiro.nl/trading/secure/v5/order/1;jsessionid=x"),
        ("update", "https://trader.degiro.nl/trading/secure/v5/order/1;jsessionid=x"),
    ],
)
def test_transport_refuses_anything_outside_the_allowlist(transport, adapter, operation, url):
    with pytest.raises(DegiroError) as raised:
        transport.send(operation, url)
    assert raised.value.code is ErrorCode.OPERATION_NOT_ALLOWED
    assert adapter.sent == []


@pytest.mark.parametrize("options", [["orders"], ["historicalOrders"], ["alerts"], []])
def test_update_reads_only_portfolio_and_cash(connected, adapter, options):
    before = len(adapter.sent)
    with pytest.raises(DegiroError) as raised:
        connected.get_update(options)
    assert raised.value.code is ErrorCode.OPERATION_NOT_ALLOWED
    assert len(adapter.sent) == before


def _hardened_trees():
    for path in sorted(HARDENED_DIR.glob("*.py")):
        yield path.name, ast.parse(path.read_text())


def test_hardened_package_imports_no_dynamic_or_order_code():
    forbidden = (
        "degiro_connector.trading.actions",
        "degiro_connector.trading.api",
        "degiro_connector.core.helpers",
        "degiro_connector.quotecast",
        "pyotp",
        "importlib",
    )
    for name, tree in _hardened_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden), (name, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), (name, alias.name)


def test_hardened_package_reads_no_environment_or_files():
    for name, tree in _hardened_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in {"open", "getenv", "environ", "__import__"}, name
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"environ", "getenv", "read_text", "read_bytes"}, name


def test_upstream_order_actions_are_gone():
    modules = {p.stem for p in ACTIONS_DIR.glob("action_*.py")}
    for gone in ("check_order", "confirm_order", "update_order", "delete_order"):
        assert f"action_{gone}" not in modules
    for constant in ("ORDER_CHECK", "ORDER_CONFIRM", "ORDER_DELETE", "ORDER_UPDATE"):
        assert not hasattr(urls, constant)


def test_file_and_env_credential_helper_is_gone():
    assert not hasattr(upstream_credentials, "build_credentials")
    source = Path(upstream_credentials.__file__).read_text()
    assert "DEGIRO_ACCOUNT" not in source
    assert "os.environ" not in source


def test_account_info_reads_the_account_path_with_the_session(connected, adapter):
    adapter.queue(reply(200, {"data": {"baseCurrency": "EUR"}}))
    body = connected.get_account_info()
    assert body == {"data": {"baseCurrency": "EUR"}}
    sent = adapter.sent[-1].request
    assert sent.method == "GET"
    assert sent.url.split("?")[0] == (
        f"https://trader.degiro.nl/trading/secure/v5/account/info/{INT_ACCOUNT};jsessionid={SESSION_ID}"
    )


def test_account_info_needs_client_details_first(client, adapter, credentials):
    adapter.queue(LOGIN_OK)
    client.login(credentials)
    with pytest.raises(DegiroError) as raised:
        client.get_account_info()
    assert raised.value.code is ErrorCode.NOT_CONNECTED
    assert len(adapter.sent) == 1
