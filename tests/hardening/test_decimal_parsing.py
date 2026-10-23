"""H-8: numbers are parsed as Decimal from their exact text."""

from datetime import date
from decimal import Decimal

import pytest

from degiro_connector.hardened import DegiroError, ErrorCode, loads_decimal

from .conftest import reply


def test_fractions_become_exact_decimals_and_integers_stay_int():
    value = loads_decimal('{"price": 0.1, "fee": 1e-2, "total": -202.46, "quantity": 3}')
    assert value == {
        "price": Decimal("0.1"),
        "fee": Decimal("0.01"),
        "total": Decimal("-202.46"),
        "quantity": 3,
    }
    assert type(value["price"]) is Decimal
    assert type(value["quantity"]) is int


def test_digits_beyond_float_precision_survive():
    assert loads_decimal('{"x": 12345678901234567.891}')["x"] == Decimal("12345678901234567.891")


@pytest.mark.parametrize(
    "text",
    ['{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"a": 1, "a": 2}', "{", "", "[1,]"],
)
def test_non_finite_duplicate_or_broken_json_is_refused(text):
    with pytest.raises(DegiroError) as raised:
        loads_decimal(text)
    assert raised.value.code is ErrorCode.SCHEMA
    assert raised.value.__context__ is None


def test_client_reads_return_decimals(connected, adapter):
    adapter.queue(
        reply(200, '{"data": [{"id": 7, "price": 101.23, "total": -202.46, "quantity": 2}]}')
    )
    history = connected.get_transactions_history(date(2026, 1, 1), date(2026, 1, 31))
    row = history["data"][0]
    assert row["price"] == Decimal("101.23")
    assert row["total"] == Decimal("-202.46")
    assert row["quantity"] == 2
