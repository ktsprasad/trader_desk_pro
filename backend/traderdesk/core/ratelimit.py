"""Token-bucket limiter, one bucket per endpoint group, so a scan can never starve order placement."""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: float | None = None, clock=time.monotonic, sleep=time.sleep):
        self.rate = float(rate_per_sec)
        self.capacity = float(burst if burst is not None else max(1.0, rate_per_sec))
        self.tokens = self.capacity
        self.clock, self.sleep = clock, sleep
        self.t = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
        self.t = now

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

    def acquire(self) -> None:
        while not self.try_acquire():
            self.sleep(max(0.01, (1 - self.tokens) / self.rate))


class Limiter:
    def __init__(self, limits: dict[str, float]):
        self.buckets = {k: TokenBucket(v) for k, v in limits.items()}

    def __call__(self, group: str) -> None:
        b = self.buckets.get(group)
        if b:
            b.acquire()
