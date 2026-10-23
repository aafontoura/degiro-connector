"""The only way the hardened client reaches the network (H-4, H-5, H-6, H-7).

Every request names an operation from ``ALLOWED_OPERATIONS``; the operation
fixes the HTTP method and the exact path shape. Before anything is sent the URL
must be ``https://trader.degiro.nl`` on port 443 with no user info. Requests
use finite connect/read timeouts plus a total cap checked between body chunks, never
follow a redirect (any 3xx fails closed, same host or not), ignore proxy, netrc
and CA settings from the environment, and read at most ``max_response_bytes``.

Nothing here logs. ``requests``/``urllib3`` log full URLs (which carry the
session ID) at DEBUG, so constructing a transport raises those loggers above
CRITICAL for the process. That is defense in depth for the short-lived child
process that owns this transport, not the fix: the fix is that this package
never logs.

The total cap is checked between chunks, so a server that trickles bytes can
hold one request somewhat past it, and DNS resolution is not covered by the
connect timeout. The hard bound is the caller's: the process running this
client must have a wall-clock deadline and be killed when it passes.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter, HTTPAdapter

from degiro_connector.core.constants.headers import HEADERS

from .errors import DegiroError, ErrorCode

ALLOWED_SCHEME = "https"
ALLOWED_HOST = "trader.degiro.nl"
ALLOWED_PORT = 443

_JSESSIONID = r";jsessionid=[^/;?#]+"

# operation -> (method, path). Nothing that places, changes or deletes an
# order, favourite or note is listed, and nothing outside this table is sent.
ALLOWED_OPERATIONS: dict[str, tuple[str, re.Pattern[str]]] = {
    "login": ("POST", re.compile(r"/login/secure/login/totp")),
    "client_details": ("GET", re.compile(r"/pa/secure/client")),
    "transactions_history": (
        "GET",
        re.compile(r"/portfolio-reports/secure/v4/transactions"),
    ),
    "account_overview": (
        "GET",
        re.compile(r"/portfolio-reports/secure/v6/accountoverview"),
    ),
    "update": ("GET", re.compile(r"/trading/secure/v5/update/\d+" + _JSESSIONID)),
    "products_info": (
        "POST",
        re.compile(r"/product_search/secure/v5/products/info"),
    ),
    "logout": ("PUT", re.compile(r"/trading/secure/logout" + _JSESSIONID)),
}

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_CHUNK_BYTES = 8 * 1024
_QUIETED_LOGGERS = ("urllib3", "requests")


@dataclass(frozen=True)
class RequestBudget:
    connect: float
    read: float
    total: float


DEFAULT_BUDGET = RequestBudget(connect=5.0, read=15.0, total=15.0)
LOGOUT_BUDGET = RequestBudget(connect=2.0, read=5.0, total=5.0)


@dataclass(frozen=True)
class TransportResponse:
    status: int
    text: str


def _quiet_http_loggers() -> None:
    for name in _QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def _url_refusal(url: str, path_pattern: re.Pattern[str]) -> ErrorCode | None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ErrorCode.HOST_NOT_ALLOWED
    if not (
        parts.scheme == ALLOWED_SCHEME
        and parts.hostname == ALLOWED_HOST
        and parts.username is None
        and parts.password is None
        and port in (None, ALLOWED_PORT)
        and not parts.fragment
    ):
        return ErrorCode.HOST_NOT_ALLOWED
    if path_pattern.fullmatch(parts.path) is None:
        return ErrorCode.OPERATION_NOT_ALLOWED
    return None


class _NoRedirectSession(requests.Session):
    """A session that never treats a response as a redirect.

    With ``allow_redirects=False`` a plain ``Session.send`` still prepares the
    next hop and reads the whole 3xx body to release the socket; returning no
    target skips both, so ``Transport.send`` sees the 3xx and refuses it.
    """

    def get_redirect_target(self, resp: requests.Response) -> None:
        return None


class Transport:
    """A locked-down ``requests.Session`` for one sync."""

    def __init__(
        self,
        adapter: BaseAdapter | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        _quiet_http_loggers()
        session = _NoRedirectSession()
        session.trust_env = False
        session.proxies = {}
        session.headers.clear()
        session.headers.update(HEADERS)
        # No plain-HTTP adapter at all; https only, no automatic retries.
        session.adapters.clear()
        session.mount("https://", adapter or HTTPAdapter(max_retries=0))
        self._session: requests.Session | None = session
        self._max_response_bytes = max_response_bytes

    @property
    def session(self) -> requests.Session | None:
        return self._session

    def send(
        self,
        operation: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        budget: RequestBudget = DEFAULT_BUDGET,
    ) -> TransportResponse:
        session = self._session
        if session is None:
            raise DegiroError(ErrorCode.CLOSED)
        allowed = ALLOWED_OPERATIONS.get(operation)
        if allowed is None:
            raise DegiroError(ErrorCode.OPERATION_NOT_ALLOWED)
        method, path_pattern = allowed
        refusal = _url_refusal(url, path_pattern)
        if refusal is not None:
            raise DegiroError(refusal)

        prepped: requests.PreparedRequest | None = None
        try:
            prepped = session.prepare_request(
                requests.Request(method=method, url=url, params=params, json=json)
            )
        except Exception:  # noqa: BLE001 - its message would carry the URL
            prepped = None
        if prepped is None:
            raise DegiroError(ErrorCode.INVALID_REQUEST)
        refusal = (
            ErrorCode.HOST_NOT_ALLOWED
            if prepped.url is None
            else _url_refusal(prepped.url, path_pattern)
        )
        if refusal is not None:
            raise DegiroError(refusal)

        failure: ErrorCode | None = None
        response: requests.Response | None = None
        started = time.monotonic()
        try:
            response = session.send(
                prepped,
                allow_redirects=False,
                proxies={},
                stream=True,
                timeout=(budget.connect, budget.read),
            )
        except requests.Timeout:
            failure = ErrorCode.TIMEOUT
        except requests.RequestException:
            failure = ErrorCode.TRANSPORT
        except Exception:  # noqa: BLE001 - an adapter bug is still a transport failure
            failure = ErrorCode.TRANSPORT
        if failure is not None or response is None:
            raise DegiroError(failure or ErrorCode.TRANSPORT)

        status = response.status_code
        if 300 <= status < 400:
            response.close()
            raise DegiroError(ErrorCode.REDIRECT_BLOCKED, status)

        body, failure = self._read_body(response, started, budget)
        if failure is not None:
            raise DegiroError(failure, status)
        return TransportResponse(status=status, text=body)

    def _read_body(
        self,
        response: requests.Response,
        started: float,
        budget: RequestBudget,
    ) -> tuple[str, ErrorCode | None]:
        chunks: list[bytes] = []
        size = 0
        failure: ErrorCode | None = None
        try:
            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                size += len(chunk)
                if size > self._max_response_bytes:
                    failure = ErrorCode.RESPONSE_TOO_LARGE
                    break
                if time.monotonic() - started > budget.total:
                    failure = ErrorCode.TIMEOUT
                    break
                chunks.append(chunk)
        except requests.Timeout:
            failure = ErrorCode.TIMEOUT
        except Exception:  # noqa: BLE001 - see send()
            failure = ErrorCode.TRANSPORT
        finally:
            response.close()
        if failure is not None:
            return "", failure
        try:
            return b"".join(chunks).decode("utf-8"), None
        except UnicodeDecodeError:
            return "", ErrorCode.SCHEMA

    def close(self) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        session.cookies.clear()
        session.headers.clear()
        session.close()
