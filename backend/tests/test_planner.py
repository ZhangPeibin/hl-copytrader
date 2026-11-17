from __future__ import annotations

from decimal import Decimal

from hlcopy.engine.planner import plan
from hlcopy.engine.types import PlanInput


def make(**overrides) -> PlanInput:
    values = dict(
        coin="BTC",
        task_status="running",
        leader_size=Decimal("1"),
        follower_size=Decimal("0"),
        ratio=Decimal("0.1"),
        max_notional=Decimal("10000"),
        sz_decimals=3,
        mark_px=Decimal("1000"),
        locked=False,
        next_retry_at_ms=None,
        now_ms=1_000,
        leverage_set=False,
        leader_leverage=Decimal("10"),
        leader_is_cross=True,
        max_leverage=20,
    )
    values.update(overrides)
    return PlanInput(**values)


def test_paused_task_short_circuits():
    action = plan(make(task_status="paused"))
    assert action.reason == "task_paused"
    assert action.orders == ()


def test_stopped_task_short_circuits():
    assert plan(make(task_status="stopped")).reason == "task_stopped"


def test_status_beats_lock():
    assert plan(make(task_status="paused", locked=True)).reason == "task_paused"


def test_leader_unavailable_is_never_flat():
    action = plan(make(leader_size=None, follower_size=Decimal("2")))

    assert action.reason == "leader_state_unavailable"
    assert action.orders == ()


def test_missing_mark_skips():
    assert plan(make(mark_px=None)).reason == "mark_unavailable"


def test_backoff_window_skips():
    action = plan(make(next_retry_at_ms=2_000, now_ms=1_500))
    assert action.reason == "backoff"
    assert action.orders == ()


def test_locked_coin_is_untouched():
    action = plan(make(locked=True, leader_size=Decimal("1"), follower_size=Decimal("0")))
    assert action.reason == "locked"
    assert action.orders == ()


def test_locked_coin_unlocks_without_orders():
    action = plan(make(locked=True, leader_size=Decimal("0"), follower_size=Decimal("0")))

    assert action.reason == "unlocked"
    assert action.unlocked is True
    assert action.orders == ()


def test_no_position_does_nothing():
    action = plan(make(leader_size=Decimal("0"), follower_size=Decimal("0")))
    assert action.reason == "no_position"
    assert action.orders == ()


def test_close_all_uses_actual_position_not_delta():
    action = plan(
        make(leader_size=Decimal("0"), follower_size=Decimal("0.001"), mark_px=Decimal("100"))
    )

    assert action.reason == "close_all"
    assert action.target == 0
    order = action.orders[0]
    assert order.side == "sell"
    assert order.size == Decimal("0.001")
    assert order.reduce_only is True


def test_close_all_for_short_sells_to_buy():
    order = plan(make(leader_size=Decimal("0"), follower_size=Decimal("-2"))).orders[0]
    assert order.side == "buy"


def test_below_min_notional_skips():
    action = plan(
        make(leader_size=Decimal("0.001"), ratio=Decimal("1"), mark_px=Decimal("100"))
    )
    assert action.reason == "below_min_notional"
    assert action.orders == ()


def test_delta_rounding_can_fall_below_min_notional():
    action = plan(
        make(
            leader_size=Decimal("0.01"),
            follower_size=Decimal("0.005"),
            ratio=Decimal("1"),
            sz_decimals=2,
            mark_px=Decimal("1000"),
        )
    )
    assert action.delta == 0
    assert action.reason == "below_min_notional"


def test_reverse_plans_two_orders():
    action = plan(
        make(
            leader_size=Decimal("-2"),
            follower_size=Decimal("1"),
            ratio=Decimal("1"),
            sz_decimals=2,
            mark_px=Decimal("100"),
        )
    )

    assert action.reason == "reverse"
    close, open_ = action.orders
    assert close.side == "sell" and close.size == Decimal("1") and close.reduce_only is True
    assert close.leg == 0
    assert open_.side == "sell" and open_.size == Decimal("2") and open_.reduce_only is False
    assert open_.leg == 1


def test_first_entry_sets_leverage_before_placing():
    action = plan(make(leader_size=Decimal("1"), ratio=Decimal("0.1"), mark_px=Decimal("1000")))

    assert action.reason == "set_leverage"
    assert action.leverage is not None
    assert action.leverage.leverage == 10
    assert action.leverage.is_cross is True
    assert action.orders[0].side == "buy"
    assert action.orders[0].size == Decimal("0.1")


def test_leverage_is_capped_by_market_maximum():
    action = plan(make(leader_leverage=Decimal("50"), max_leverage=20, mark_px=Decimal("1000")))
    assert action.leverage.leverage == 20


def test_leverage_falls_back_to_market_maximum():
    action = plan(make(leader_leverage=None, max_leverage=20, mark_px=Decimal("1000")))
    assert action.leverage.leverage == 20


def test_increase_is_not_reduce_only():
    action = plan(
        make(
            leader_size=Decimal("1"),
            follower_size=Decimal("0.3"),
            ratio=Decimal("1"),
            leverage_set=True,
            mark_px=Decimal("100"),
        )
    )

    assert action.reason == "place"
    assert action.orders[0].side == "buy"
    assert action.orders[0].reduce_only is False


def test_reduce_is_reduce_only():
    action = plan(
        make(
            leader_size=Decimal("0.3"),
            follower_size=Decimal("1"),
            ratio=Decimal("1"),
            leverage_set=True,
            mark_px=Decimal("100"),
        )
    )

    assert action.reason == "place"
    assert action.orders[0].side == "sell"
    assert action.orders[0].reduce_only is True


def test_max_notional_truncates_target():
    action = plan(
        make(
            leader_size=Decimal("10"),
            ratio=Decimal("1"),
            mark_px=Decimal("100"),
            max_notional=Decimal("500"),
            leverage_set=True,
        )
    )

    assert action.truncated is True
    assert action.target == Decimal("5")
    assert action.reason == "place"


def test_truncation_never_exceeds_max_notional():
    action = plan(
        make(
            leader_size=Decimal("10"),
            ratio=Decimal("1"),
            mark_px=Decimal("300"),
            max_notional=Decimal("500"),
            sz_decimals=2,
        )
    )

    assert action.target == Decimal("1.66")
    assert action.target * Decimal("300") <= Decimal("500")


def test_truncated_target_can_fall_below_min_notional():
    action = plan(
        make(
            leader_size=Decimal("100"),
            ratio=Decimal("1"),
            mark_px=Decimal("100"),
            max_notional=Decimal("5"),
            sz_decimals=4,
        )
    )

    assert action.target == Decimal("0.05")
    assert action.reason == "below_min_notional"


def test_ratio_scales_leader_size():
    action = plan(
        make(leader_size=Decimal("3"), ratio=Decimal("0.2"), mark_px=Decimal("1000"), leverage_set=True)
    )
    assert action.target == Decimal("0.6")


def test_short_leader_produces_sell():
    action = plan(
        make(leader_size=Decimal("-1"), ratio=Decimal("0.1"), mark_px=Decimal("1000"))
    )
    assert action.target == Decimal("-0.1")
    assert action.orders[0].side == "sell"
    assert action.orders[0].reduce_only is False
