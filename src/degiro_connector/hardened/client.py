"""The fork's supported surface: read-only DEGIRO access for one sync (H-7).

``ReadOnlyTradingClient`` exposes exactly: ``login`` (username, password and
a six-digit one-time code), ``get_client_details``, ``get_account_info``,
``get_transactions_history``, ``get_account_overview``, ``get_update`` (portfolio
and cash only), ``get_products_info`` and ``close``. It does not use the
upstream dynamic ``API`` or any upstream action, so no order, favourite or note
operation is reachable from it, and its transport refuses anything outside its
allowlist.

Responses come back as Decimal-safe parsed JSON (H-8); the caller normalizes
them. Failures are ``DegiroError`` codes only (H-2). Nothing is logged (H-1).
``close()`` logs out within a short budget and then clears the session ID, the
account ID, cookies and the HTTP session even when logout fails (H-10).
"""

import re
from collections.abc import Iterable, Sequence
from datetime import date
from types import TracebackType
from typing import Any

from degiro_connector.core.constants import urls

from .credentials import OtpCredentials
from .decimal_json import loads_decimal
from .errors import DegiroError, ErrorCode
from .transport import LOGOUT_BUDGET, Transport, TransportResponse

LOGIN_TOTP_URL = urls.LOGIN + "/totp"
UPDATE_OPTIONS = frozenset({"cashFunds", "portfolio", "totalPortfolio"})
MAX_PRODUCT_IDS = 500
# The session ID is put into paths and query strings; accept only a shape
# that cannot change where a request goes.
_SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

_LOGIN_STATUS_OTP_REQUIRED = 6
_LOGIN_STATUS_IN_APP_APPROVAL = 12


def _degiro_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _login_failure(response: TransportResponse) -> DegiroError:
    if response.status == 405:
        # Upstream fetches the maintenance page to build a message; its text
        # is not needed for a sanitized error, so no extra request is made.
        return DegiroError(ErrorCode.MAINTENANCE, response.status)
    body: Any = None
    try:
        body = loads_decimal(response.text)
    except DegiroError:
        body = None
    if isinstance(body, dict):
        if body.get("captchaRequired") is True:
            return DegiroError(ErrorCode.CAPTCHA_REQUIRED, response.status)
        status = body.get("status")
        if status == _LOGIN_STATUS_OTP_REQUIRED:
            return DegiroError(ErrorCode.OTP_REQUIRED, response.status)
        if status == _LOGIN_STATUS_IN_APP_APPROVAL:
            return DegiroError(ErrorCode.IN_APP_APPROVAL_REQUIRED, response.status)
    return DegiroError(ErrorCode.LOGIN_REJECTED, response.status)


def _status_failure(status: int) -> DegiroError:
    if status in (401, 403):
        return DegiroError(ErrorCode.UNAUTHORIZED, status)
    if status == 429:
        return DegiroError(ErrorCode.RATE_LIMITED, status)
    if status >= 500:
        return DegiroError(ErrorCode.SERVER_ERROR, status)
    return DegiroError(ErrorCode.HTTP_STATUS, status)


class ReadOnlyTradingClient:
    """One login, a few reads, one logout."""

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else Transport()
        self._session_id: str | None = None
        self._int_account: int | None = None
        self._closed = False

    # -- lifecycle -----------------------------------------------------------

    def login(self, credentials: OtpCredentials) -> None:
        if self._closed:
            credentials.clear()
            raise DegiroError(ErrorCode.CLOSED)
        if self._session_id is not None:
            credentials.clear()
            raise DegiroError(ErrorCode.ALREADY_CONNECTED)
        username, password, otp = credentials.take()
        # The same body upstream sends (including its `queryTarams` spelling),
        # so the hardened path does not change what DEGIRO sees.
        payload: dict[str, Any] = {
            "username": username,
            "password": password,
            "isPassCodeReset": False,
            "isRedirectToMobile": False,
            "queryTarams": {},
            "oneTimePassword": otp,
        }
        del username, password, otp
        try:
            response = self._transport.send("login", LOGIN_TOTP_URL, json=payload)
        finally:
            payload.clear()

        if response.status != 200:
            raise _login_failure(response)
        body = loads_decimal(response.text)
        session_id = body.get("sessionId") if isinstance(body, dict) else None
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise DegiroError(ErrorCode.SCHEMA)
        self._session_id = session_id

    def close(self) -> bool | None:
        """Log out, then forget everything.

        Returns ``True`` when DEGIRO confirmed the logout, ``False`` when it
        did not (the remote session may still be live until it expires), and
        ``None`` when there was no session to log out.
        """
        if self._closed:
            return None
        session_id = self._session_id
        int_account = self._int_account
        confirmed: bool | None = None
        try:
            if session_id is not None:
                confirmed = False
                response = self._transport.send(
                    "logout",
                    f"{urls.LOGOUT};jsessionid={session_id}",
                    params={"intAccount": int_account, "sessionId": session_id},
                    budget=LOGOUT_BUDGET,
                )
                confirmed = response.status == 200
        except DegiroError:
            confirmed = False
        finally:
            self._session_id = None
            self._int_account = None
            self._closed = True
            self._transport.close()
        return confirmed

    def __enter__(self) -> "ReadOnlyTradingClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- reads ---------------------------------------------------------------

    def get_client_details(self) -> Any:
        session_id = self._require_session()
        body = self._read(
            "client_details",
            urls.CLIENT_DETAILS,
            params={"sessionId": session_id},
        )
        data = body.get("data") if isinstance(body, dict) else None
        int_account = data.get("intAccount") if isinstance(data, dict) else None
        if isinstance(int_account, bool) or not isinstance(int_account, int):
            raise DegiroError(ErrorCode.SCHEMA)
        self._int_account = int_account
        return body

    def get_account_info(self) -> Any:
        """Account settings, including ``baseCurrency``, which client details lack."""
        session_id = self._require_session()
        int_account = self._require_account()
        return self._read(
            "account_info",
            f"{urls.ACCOUNT_INFO}/{int_account};jsessionid={session_id}",
            params={},
        )

    def get_transactions_history(self, from_date: date, to_date: date) -> Any:
        """Executions in the window, ungrouped (one row per fill)."""
        params = self._window_params(from_date, to_date)
        # Python False, as upstream sends it, so the query string is unchanged.
        params["groupTransactionsByOrder"] = False
        return self._read("transactions_history", urls.TRANSACTIONS_HISTORY, params)

    def get_account_overview(self, from_date: date, to_date: date) -> Any:
        """Cash movements in the window."""
        params = self._window_params(from_date, to_date)
        return self._read("account_overview", urls.ACCOUNT_OVERVIEW, params)

    def get_update(self, options: Iterable[str]) -> Any:
        """Current portfolio and cash, from the trading update endpoint."""
        session_id = self._require_session()
        int_account = self._require_account()
        selected = sorted(set(options))
        if not selected or not set(selected) <= UPDATE_OPTIONS:
            raise DegiroError(ErrorCode.OPERATION_NOT_ALLOWED)
        params: dict[str, Any] = {option: 0 for option in selected}
        params.update({"intAccount": int_account, "sessionId": session_id})
        return self._read(
            "update",
            f"{urls.UPDATE}/{int_account};jsessionid={session_id}",
            params,
        )

    def get_products_info(self, product_ids: Sequence[int]) -> Any:
        session_id = self._require_session()
        int_account = self._require_account()
        ids = list(product_ids)
        if (
            not ids
            or len(ids) > MAX_PRODUCT_IDS
            or any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids)
        ):
            raise DegiroError(ErrorCode.INVALID_REQUEST)
        return self._read(
            "products_info",
            urls.PRODUCTS_INFO,
            params={"intAccount": int_account, "sessionId": session_id},
            json=ids,
        )

    # -- helpers -------------------------------------------------------------

    def _window_params(self, from_date: date, to_date: date) -> dict[str, Any]:
        session_id = self._require_session()
        int_account = self._require_account()
        if (
            not isinstance(from_date, date)
            or not isinstance(to_date, date)
            or from_date > to_date
        ):
            raise DegiroError(ErrorCode.INVALID_REQUEST)
        return {
            "fromDate": _degiro_date(from_date),
            "toDate": _degiro_date(to_date),
            "intAccount": int_account,
            "sessionId": session_id,
        }

    def _read(
        self,
        operation: str,
        url: str,
        params: dict[str, Any],
        json: Any = None,
    ) -> Any:
        response = self._transport.send(operation, url, params=params, json=json)
        if response.status != 200:
            raise _status_failure(response.status)
        return loads_decimal(response.text)

    def _require_open(self) -> None:
        if self._closed:
            raise DegiroError(ErrorCode.CLOSED)

    def _require_session(self) -> str:
        self._require_open()
        if self._session_id is None:
            raise DegiroError(ErrorCode.NOT_CONNECTED)
        return self._session_id

    def _require_account(self) -> int:
        if self._int_account is None:
            raise DegiroError(ErrorCode.NOT_CONNECTED)
        return self._int_account

    def __repr__(self) -> str:
        state = "closed" if self._closed else (
            "connected" if self._session_id is not None else "new"
        )
        return f"ReadOnlyTradingClient(<{state}>)"
