from __future__ import annotations

from decimal import Decimal

import pytest

from hlcopy.hl.rounding import RoundingError, round_price, round_size, sign_of


@pytest.mark.parametrize(
    ("raw", "sz", "expected"),
    [
        ("1.98765", 0, "1"),
        ("1.98765", 2, "1.98"),
        ("-1.98765", 2, "-1.98"),
        ("0.005", 2, "0.00"),
        ("-0.005", 2, "-0.00"),
        ("12", 3, "12.000"),
    ],
)
def test_round_size_truncates_toward_zero(raw, sz, expected):
    assert round_size(Decimal(raw), sz) == Decimal(expected)


@pytest.mark.parametrize(
    ("raw", "sz", "side", "expected"),
    [
        ("65000.55", 5, "buy", "65000.5"),
        ("65000.55", 5, "sell", "65000.6"),
        ("123456.7", 4, "buy", "123450"),
        ("123456.7", 4, "sell", "123460"),
        ("1234567.8", 0, "buy", "1234500"),
        ("1234567.8", 0, "sell", "1234600"),
        ("123456", 4, "buy", "123456"),
        ("123456", 4, "sell", "123456"),
        ("0.0001234567", 0, "buy", "0.000123"),
        ("0.0001234567", 0, "sell", "0.000124"),
    ],
)
def test_round_price_keeps_five_significant_figures_and_decimals(raw, sz, side, expected):
    assert round_price(Decimal(raw), sz, side=side) == Decimal(expected)


@pytest.mark.parametrize("raw", ["0", "-1", "0.00000001"])
def test_round_price_rejects_non_positive(raw):
    with pytest.raises(RoundingError):
        round_price(Decimal(raw), 0, side="buy")


def test_round_price_rejects_unknown_side():
    with pytest.raises(RoundingError):
        round_price(Decimal("1"), 0, side="hold")


def test_sign_of():
    assert sign_of(Decimal("1.5")) == 1
    assert sign_of(Decimal("-1.5")) == -1
    assert sign_of(Decimal("0")) == 0
