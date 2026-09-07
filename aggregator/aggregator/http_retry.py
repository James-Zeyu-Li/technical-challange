"""Generic, source-agnostic retry engine sitting between transport and pagination."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class HTTPResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None


class RetryExhaustedError(Exception):
    """Base for every way fetch_with_retry can ultimately fail, so callers
    can catch one type instead of enumerating HTTPError/TimeoutError/etc."""


class HTTPError(RetryExhaustedError):
    """Raised when a request fails without being retried, or retries are exhausted."""

    def __init__(self, response: HTTPResponse, attempts: int) -> None:
        self.response = response
        self.attempts = attempts
        super().__init__(f"request failed with status {response.status_code} after {attempts} attempt(s)")


class TransportError(RetryExhaustedError):
    """Raised when a connection/timeout error persists until retries are exhausted."""

    def __init__(self, original_error: Exception, attempts: int) -> None:
        self.original_error = original_error
        self.attempts = attempts
        super().__init__(f"transport failed after {attempts} attempt(s): {original_error}")


def is_retryable(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES


def get_retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    # A negative wait makes no sense and would crash a real sleep_fn
    # (time.sleep raises on negative input) - treat it as absent instead.
    return parsed if parsed >= 0 else None


def fetch_with_retry(
    transport: Callable[[], HTTPResponse],
    max_attempts: int = MAX_ATTEMPTS,
    default_backoff: float = DEFAULT_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> HTTPResponse:
    """Call `transport()` up to `max_attempts` times, retrying retryable failures.

    `transport` performs one HTTP attempt and either returns an HTTPResponse
    or raises TimeoutError/ConnectionError (treated the same as a retryable
    status code, and wrapped in TransportError once retries are exhausted).
    Retry-After is honored when present and non-negative; otherwise falls
    back to `default_backoff`.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    for attempt in range(1, max_attempts + 1):
        try:
            response = transport()
        except (TimeoutError, ConnectionError) as exc:
            if attempt >= max_attempts:
                logger.error("attempt %d/%d failed, giving up: %s", attempt, max_attempts, exc)
                raise TransportError(exc, attempt) from exc
            logger.warning(
                "attempt %d/%d failed (%s); retrying in %.2fs", attempt, max_attempts, exc, default_backoff
            )
            sleep_fn(default_backoff)
            continue

        if response.status_code < 400:
            return response

        if not is_retryable(response.status_code) or attempt >= max_attempts:
            logger.error(
                "attempt %d/%d failed with status %d, giving up", attempt, max_attempts, response.status_code
            )
            raise HTTPError(response, attempt)

        wait = get_retry_after(response.headers)
        wait_seconds = wait if wait is not None else default_backoff
        logger.warning(
            "attempt %d/%d failed with status %d; retrying in %.2fs",
            attempt,
            max_attempts,
            response.status_code,
            wait_seconds,
        )
        sleep_fn(wait_seconds)

    raise AssertionError("unreachable")  # loop always returns or raises above
