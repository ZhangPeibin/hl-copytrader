from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal


class RoundingError(ValueError):
    pass


def size_step(sz_decimals: int) -> Decimal:
    if sz_decimals < 0:
        raise RoundingError("szDecimals must be >= 0")
    return Decimal(1).scaleb(-sz_decimals)


def round_size(size: Decimal, sz_decimals: int) -> Decimal:
    return size.quantize(size_step(sz_decimals), rounding=ROUND_DOWN)


def max_price_decimals(sz_decimals: int) -> int:
    return max(0, 6 - sz_decimals)


def round_price(price: Decimal, sz_decimals: int, *, side: str) -> Decimal:
    if side not in ("buy", "sell"):
        raise RoundingError(f"unknown side {side!r}")
    if price <= 0:
        raise RoundingError("price must be positive")

    rounding = ROUND_DOWN if side == "buy" else ROUND_UP
    rounded = price

    if price != price.to_integral_value():
        exponent = price.adjusted() - 4
        if exponent > 0:
            rounded = price.quantize(Decimal(1).scaleb(exponent), rounding=rounding)

    decimals = max_price_decimals(sz_decimals)
    rounded = rounded.quantize(Decimal(1).scaleb(-decimals), rounding=rounding)

    if rounded <= 0:
        raise RoundingError("price rounds to zero")
    return rounded


def sign_of(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
