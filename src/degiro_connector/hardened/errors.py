"""Sanitized, typed errors for the hardened read-only client (H-2).

An error carries a code and, at most, an HTTP status code. It never carries a
URL, a request or response body, a header, a cookie or a credential: DEGIRO
puts the session ID in query strings and paths, so any of those can leak it.

Errors are raised outside ``except`` blocks, so neither ``__cause__`` nor
``__context__`` holds the underlying ``requests`` exception (whose message
usually contains the full URL).
"""

from enum import Enum


class ErrorCode(str, Enum):
    INVALID_CREDENTIALS = "invalid_credentials"
    INVALID_REQUEST = "invalid_request"
    ALREADY_CONNECTED = "already_connected"
    NOT_CONNECTED = "not_connected"
    CLOSED = "closed"
    LOGIN_REJECTED = "login_rejected"
    OTP_REQUIRED = "otp_required"
    IN_APP_APPROVAL_REQUIRED = "in_app_approval_required"
    CAPTCHA_REQUIRED = "captcha_required"
    MAINTENANCE = "maintenance"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    HTTP_STATUS = "http_status"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    HOST_NOT_ALLOWED = "host_not_allowed"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    REDIRECT_BLOCKED = "redirect_blocked"
    RESPONSE_TOO_LARGE = "response_too_large"
    SCHEMA = "schema"


class DegiroError(Exception):
    """A failure named by code, safe to log, persist or show."""

    def __init__(self, code: ErrorCode, status: int | None = None) -> None:
        if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
            status = None
        self.code = code
        self.status = status
        message = code.value if status is None else f"{code.value} (HTTP {status})"
        super().__init__(message)

    def __reduce__(self):
        # Rebuild from code and status, so the error survives a process hop.
        return (DegiroError, (self.code, self.status))

    def __repr__(self) -> str:
        return f"DegiroError({self.code.value!r}, status={self.status!r})"
