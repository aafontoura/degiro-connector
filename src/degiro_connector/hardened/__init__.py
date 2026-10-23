"""Hardened, read-only DEGIRO access: the fork's only supported surface.

See ``HARDENING.md`` at the repository root for the patch set and its tests.
"""

from .client import ReadOnlyTradingClient
from .credentials import OtpCredentials
from .decimal_json import loads_decimal
from .errors import DegiroError, ErrorCode

__all__ = [
    "DegiroError",
    "ErrorCode",
    "OtpCredentials",
    "ReadOnlyTradingClient",
    "loads_decimal",
]
