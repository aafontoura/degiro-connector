"""Decimal-safe JSON parsing (H-8).

Every JSON number with a fraction or exponent becomes a ``Decimal`` taken from
its exact text, never a float. Integers stay ``int``. ``NaN``, ``Infinity`` and
duplicate object keys are refused rather than guessed at.
"""

import json
from decimal import Decimal
from typing import Any

from .errors import DegiroError, ErrorCode


def _refuse_constant(name: str) -> Any:
    raise ValueError("non-finite number")


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def loads_decimal(text: str) -> Any:
    value: Any = None
    failed = False
    try:
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_refuse_constant,
            object_pairs_hook=_refuse_duplicates,
        )
    except (ValueError, TypeError, RecursionError):
        failed = True
    if failed:
        raise DegiroError(ErrorCode.SCHEMA)
    return value
