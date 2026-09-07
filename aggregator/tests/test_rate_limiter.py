"""TDD scenarios for RateLimiter (aggregator/rate_limiter.py), not yet
implemented.

RateLimiter controls one thing: the minimum time gap between successive
requests to a single source. It doesn't know about HTTP, retries, or
pagination - it's told "wait if needed" before a request and "here's what
just happened" (optionally with rate-limit headers) after one.
"""

import unittest
from unittest.mock import Mock

from aggregator.rate_limiter import RateLimiter


class TestNoThrottling(unittest.TestCase):
    def test_no_default_interval_means_never_waits(self):
        """With no default interval configured, wait_if_needed never sleeps, no matter how many requests are recorded."""
        limiter = RateLimiter(default_min_interval=None, now_fn=Mock(return_value=100.0))
        sleep_fn = Mock()

        limiter.wait_if_needed(sleep_fn)
        limiter.record_request()
        limiter.wait_if_needed(sleep_fn)
        limiter.record_request()
        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_not_called()


class TestStaticInterval(unittest.TestCase):
    def test_never_waits_before_the_first_request(self):
        """There's nothing to space out yet on the very first call."""
        limiter = RateLimiter(default_min_interval=0.6, now_fn=Mock(return_value=100.0))
        sleep_fn = Mock()

        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_not_called()

    def test_waits_the_default_interval_when_no_time_has_elapsed(self):
        """With a clock that never advances, the full default interval is waited before the next request."""
        now_fn = Mock(return_value=100.0)
        limiter = RateLimiter(default_min_interval=0.6, now_fn=now_fn)
        sleep_fn = Mock()

        limiter.wait_if_needed(sleep_fn)
        limiter.record_request()
        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_called_once_with(0.6)

    def test_does_not_wait_if_enough_time_already_elapsed(self):
        """If real elapsed time already exceeds the interval, no (or negative) wait is added."""
        now_fn = Mock(side_effect=[0.0, 10.0])
        limiter = RateLimiter(default_min_interval=0.6, now_fn=now_fn)
        sleep_fn = Mock()

        limiter.record_request()
        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_not_called()


class TestDynamicIntervalFromHeaders(unittest.TestCase):
    """The source's own response headers (X-RateLimit-Limit/X-RateLimit-Window)
    are a more trustworthy source of truth than a hardcoded guess."""

    def test_derives_interval_from_rate_limit_headers(self):
        """X-RateLimit-Limit: 2, X-RateLimit-Window: 1 means at most 2 requests/second - the derived interval (with margin) replaces the static default."""
        now_fn = Mock(return_value=100.0)
        limiter = RateLimiter(default_min_interval=5.0, now_fn=now_fn)
        sleep_fn = Mock()

        limiter.record_request(headers={"X-RateLimit-Limit": "2", "X-RateLimit-Window": "1"})
        limiter.wait_if_needed(sleep_fn)

        # Mathematical minimum is 1/2 = 0.5s; expect a small margin above it,
        # not the unrelated static default of 5.0s.
        sleep_fn.assert_called_once()
        waited = sleep_fn.call_args.args[0]
        self.assertGreater(waited, 0.5)
        self.assertLess(waited, 1.0)

    def test_missing_headers_keeps_using_the_static_default(self):
        """record_request() with no headers at all doesn't change the interval."""
        now_fn = Mock(return_value=100.0)
        limiter = RateLimiter(default_min_interval=0.6, now_fn=now_fn)
        sleep_fn = Mock()

        limiter.record_request()
        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_called_once_with(0.6)

    def test_malformed_headers_keep_using_the_static_default(self):
        """Non-numeric X-RateLimit-* values are ignored rather than crashing or corrupting the interval."""
        now_fn = Mock(return_value=100.0)
        limiter = RateLimiter(default_min_interval=0.6, now_fn=now_fn)
        sleep_fn = Mock()

        limiter.record_request(headers={"X-RateLimit-Limit": "not-a-number", "X-RateLimit-Window": "1"})
        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_called_once_with(0.6)

    def test_zero_limit_header_keeps_using_the_static_default(self):
        """A nonsensical zero/negative limit is ignored rather than producing a division error or an infinite/negative interval."""
        now_fn = Mock(return_value=100.0)
        limiter = RateLimiter(default_min_interval=0.6, now_fn=now_fn)
        sleep_fn = Mock()

        limiter.record_request(headers={"X-RateLimit-Limit": "0", "X-RateLimit-Window": "1"})
        limiter.wait_if_needed(sleep_fn)

        sleep_fn.assert_called_once_with(0.6)


if __name__ == "__main__":
    unittest.main()
