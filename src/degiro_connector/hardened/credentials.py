"""Credentials for one OTP login (H-3, H-9).

The one-time password is a string of exactly six ASCII digits, so ``012345``
keeps its leading zero and ``000000`` is a code like any other. Nothing here
reads the environment or a file: the caller constructs credentials from what
the user typed.

``take()`` hands the values out once and drops the references. Releasing a
reference is not erasing memory; CPython cannot erase a ``str``.
"""

from .errors import DegiroError, ErrorCode

OTP_LENGTH = 6


def is_valid_otp(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == OTP_LENGTH
        and value.isascii()
        and value.isdigit()
    )


class OtpCredentials:
    """Username, password and a one-time code, consumed by one login."""

    __slots__ = ("_username", "_password", "_otp")

    def __init__(self, username: str, password: str, otp: str) -> None:
        if not isinstance(username, str) or not username:
            raise DegiroError(ErrorCode.INVALID_CREDENTIALS)
        if not isinstance(password, str) or not password:
            raise DegiroError(ErrorCode.INVALID_CREDENTIALS)
        if not is_valid_otp(otp):
            raise DegiroError(ErrorCode.INVALID_CREDENTIALS)
        self._username: str | None = username
        self._password: str | None = password
        self._otp: str | None = otp

    @property
    def is_spent(self) -> bool:
        return self._password is None

    def take(self) -> tuple[str, str, str]:
        username, password, otp = self._username, self._password, self._otp
        self.clear()
        if username is None or password is None or otp is None:
            raise DegiroError(ErrorCode.INVALID_CREDENTIALS)
        return username, password, otp

    def clear(self) -> None:
        self._username = None
        self._password = None
        self._otp = None

    def __repr__(self) -> str:
        return "OtpCredentials(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("OtpCredentials cannot be pickled")
