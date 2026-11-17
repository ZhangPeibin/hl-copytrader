from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if capacity <= 0 or refill_per_sec <= 0:
            raise ValueError("capacity and refill_per_sec must be positive")
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._updated = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self, weight: float = 1.0) -> float:
        if weight > self.capacity:
            raise ValueError("weight exceeds bucket capacity")
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.refill_per_sec
                )
                self._updated = now
                if self._tokens >= weight:
                    self._tokens -= weight
                    return waited
                delay = (weight - self._tokens) / self.refill_per_sec
            self._sleep(delay)
            waited += delay
