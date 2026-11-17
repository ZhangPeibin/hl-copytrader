from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MIN_NOTIONAL = Decimal(10)


@dataclass(frozen=True)
class OrderIntent:
    side: str
    size: Decimal
    reduce_only: bool
    leg: int = 0


@dataclass(frozen=True)
class LeverageIntent:
    is_cross: bool
    leverage: int


@dataclass(frozen=True)
class Action:
    coin: str
    reason: str
    target: Decimal
    delta: Decimal
    orders: tuple[OrderIntent, ...] = ()
    leverage: LeverageIntent | None = None
    unlocked: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class PlanInput:
    coin: str
    task_status: str
    leader_size: Decimal | None
    follower_size: Decimal
    ratio: Decimal
    max_notional: Decimal
    sz_decimals: int
    mark_px: Decimal | None
    locked: bool = False
    next_retry_at_ms: int | None = None
    now_ms: int = 0
    leverage_set: bool = False
    leader_leverage: Decimal | None = None
    leader_is_cross: bool = True
    max_leverage: int = 1
