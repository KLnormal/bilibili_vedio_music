"""Token-bucket bandwidth limiter (plan section 10).

The limiter provides a **maximum** bandwidth, not a fixed speed. Download
workers acquire tokens before writing each chunk; when the bucket is empty
they sleep until tokens are replenished. The rate can be changed at runtime
via ``set_rate`` (the TUI ``limit`` command calls this).
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class RateLimiter:
    def __init__(self, bytes_per_second: float, burst_multiplier: float = 1.0):
        self._lock = threading.Lock()
        self._rate = max(1.0, float(bytes_per_second))
        self._capacity = self._rate * max(0.1, burst_multiplier)
        self._tokens = self._capacity
        self._last = time.monotonic()

    @property
    def rate(self) -> float:
        """Current rate in bytes/second."""
        with self._lock:
            return self._rate

    def set_rate(self, bytes_per_second: float) -> None:
        with self._lock:
            self._replenish_locked()
            self._rate = max(1.0, float(bytes_per_second))
            self._capacity = self._rate

    def acquire(self, amount: int) -> None:
        """Block until ``amount`` tokens are available, then consume them."""
        if amount <= 0:
            return
        with self._lock:
            while True:
                self._replenish_locked()
                if self._tokens >= amount:
                    self._tokens -= amount
                    return
                # Sleep just enough to accumulate the missing tokens.
                missing = amount - self._tokens
                time.sleep(min(0.5, missing / self._rate))

    def _replenish_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)


def mbps_to_bps(mbps: float) -> float:
    """Convert MB/s to bytes/s (1 MB = 1024 * 1024 bytes)."""
    return float(mbps) * 1024 * 1024
