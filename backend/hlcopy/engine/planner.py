from __future__ import annotations

from decimal import Decimal

from hlcopy.engine.types import (
    MIN_NOTIONAL,
    Action,
    LeverageIntent,
    OrderIntent,
    PlanInput,
)
from hlcopy.hl.rounding import round_size, sign_of

STATUS_REASONS = {"paused": "task_paused", "stopped": "task_stopped"}


def _skip(inp: PlanInput, reason: str, target: Decimal, delta: Decimal, *, truncated: bool) -> Action:
    return Action(coin=inp.coin, reason=reason, target=target, delta=delta, truncated=truncated)


def _cap_notional(
    target: Decimal, mark: Decimal, max_notional: Decimal, sz_decimals: int
) -> Decimal:
    if abs(target) * mark <= max_notional:
        return target
    capped = sign_of(target) * (max_notional / mark)
    return round_size(capped, sz_decimals)


def plan(inp: PlanInput) -> Action:
    if inp.task_status != "running":
        return _skip(inp, STATUS_REASONS.get(inp.task_status, "task_stopped"), Decimal(0), Decimal(0), truncated=False)

    if inp.leader_size is None:
        return _skip(inp, "leader_state_unavailable", Decimal(0), Decimal(0), truncated=False)

    raw_target = round_size(inp.leader_size * inp.ratio, inp.sz_decimals)

    if inp.mark_px is None:
        return _skip(inp, "mark_unavailable", raw_target, Decimal(0), truncated=False)

    target = _cap_notional(raw_target, inp.mark_px, inp.max_notional, inp.sz_decimals)
    truncated = target != raw_target
    delta = round_size(target - inp.follower_size, inp.sz_decimals)

    if inp.next_retry_at_ms is not None and inp.next_retry_at_ms > inp.now_ms:
        return _skip(inp, "backoff", target, delta, truncated=truncated)

    if inp.locked and inp.leader_size != 0:
        return _skip(inp, "locked", target, delta, truncated=truncated)

    if inp.locked and inp.leader_size == 0:
        return Action(
            coin=inp.coin,
            reason="unlocked",
            target=target,
            delta=delta,
            unlocked=True,
            truncated=truncated,
        )

    if target == 0 and inp.follower_size == 0:
        return _skip(inp, "no_position", target, delta, truncated=truncated)

    if target == 0:
        size = abs(inp.follower_size)
        side = "sell" if inp.follower_size > 0 else "buy"
        return Action(
            coin=inp.coin,
            reason="close_all",
            target=target,
            delta=delta,
            orders=(OrderIntent(side=side, size=size, reduce_only=True, leg=0),),
            truncated=truncated,
        )

    if abs(delta) * inp.mark_px < MIN_NOTIONAL:
        return _skip(inp, "below_min_notional", target, delta, truncated=truncated)

    if inp.follower_size != 0 and sign_of(target) != sign_of(inp.follower_size):
        close_side = "sell" if inp.follower_size > 0 else "buy"
        open_side = "buy" if target > 0 else "sell"
        return Action(
            coin=inp.coin,
            reason="reverse",
            target=target,
            delta=delta,
            orders=(
                OrderIntent(side=close_side, size=abs(inp.follower_size), reduce_only=True, leg=0),
                OrderIntent(side=open_side, size=abs(target), reduce_only=False, leg=1),
            ),
            truncated=truncated,
        )

    side = "buy" if delta > 0 else "sell"
    reduce_only = inp.follower_size != 0 and sign_of(delta) != sign_of(inp.follower_size)
    order = OrderIntent(side=side, size=abs(delta), reduce_only=reduce_only, leg=0)

    if not inp.leverage_set:
        leverage = int(min(inp.leader_leverage or Decimal(inp.max_leverage), Decimal(inp.max_leverage)))
        return Action(
            coin=inp.coin,
            reason="set_leverage",
            target=target,
            delta=delta,
            orders=(order,),
            leverage=LeverageIntent(is_cross=inp.leader_is_cross, leverage=max(1, leverage)),
            truncated=truncated,
        )

    return Action(
        coin=inp.coin,
        reason="place",
        target=target,
        delta=delta,
        orders=(order,),
        truncated=truncated,
    )
