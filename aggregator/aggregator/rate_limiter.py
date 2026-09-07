"""A small, source-agnostic rate limiter: paces successive requests to one source.

It limits exactly one thing: the minimum time gap between consecutive
requests. It has no knowledge of HTTP, retries, or pagination - callers
tell it "wait if needed" before a request and "here's what just happened"
(optionally with a source's own rate-limit response headers) after one.

Each source gets its own RateLimiter instance with its own state (last
request time, learned interval) - state is never shared across sources or
across concurrent callers of a single source.
"""

from __future__ import annotations

import time
from collections.abc import Callable

# Margin above the mathematical minimum spacing (window / limit) to absorb
# timing jitter, e.g. clock resolution or scheduling delay.
MARGIN_MULTIPLIER = 1.2


class RateLimiter:
    def __init__(
        self,
        default_min_interval: float | None,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = default_min_interval
        self._now_fn = now_fn
        self._last_request_at: float | None = None

    def wait_if_needed(self, sleep_fn: Callable[[float], None]) -> None:
        if self._interval is None or self._last_request_at is None:
            return
        wait = self._interval - (self._now_fn() - self._last_request_at)
        if wait > 0:
            sleep_fn(wait)

    def record_request(self, headers: dict[str, str] | None = None) -> None:
        self._last_request_at = self._now_fn()
        derived = self._derive_interval(headers) if headers else None
        if derived is not None:
            self._interval = derived

    @staticmethod
    def _derive_interval(headers: dict[str, str]) -> float | None:
        limit_raw = headers.get("X-RateLimit-Limit")
        window_raw = headers.get("X-RateLimit-Window")
        if limit_raw is None or window_raw is None:
            return None
        try:
            limit = float(limit_raw)
            window = float(window_raw)
        except ValueError:
            return None
        if limit <= 0 or window <= 0:
            return None
        return (window / limit) * MARGIN_MULTIPLIER
