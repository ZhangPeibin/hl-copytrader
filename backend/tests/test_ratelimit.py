from __future__ import annotations

import pytest

from hlcopy.hl.ratelimit import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_consumes_capacity_without_waiting():
    clock = FakeClock()
    bucket = TokenBucket(10, 1, clock=clock, sleep=clock.sleep)

    assert bucket.acquire(4) == 0
    assert bucket.acquire(6) == 0


def test_waits_for_refill():
    clock = FakeClock()
    bucket = TokenBucket(10, 1, clock=clock, sleep=clock.sleep)

    bucket.acquire(10)
    waited = bucket.acquire(5)

    assert waited == pytest.approx(5.0)
    assert clock.t == pytest.approx(5.0)


def test_refill_is_capped_at_capacity():
    clock = FakeClock()
    bucket = TokenBucket(10, 100, clock=clock, sleep=clock.sleep)

    bucket.acquire(10)
    clock.t += 60
    assert bucket.acquire(10) == 0
    assert bucket.acquire(10) == pytest.approx(0.1)


def test_rejects_weight_above_capacity():
    bucket = TokenBucket(10, 1, clock=FakeClock(), sleep=lambda _: None)

    with pytest.raises(ValueError):
        bucket.acquire(11)


def test_rejects_invalid_construction():
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        TokenBucket(1, 0)
