from __future__ import annotations

from decimal import Decimal


def canonical(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
