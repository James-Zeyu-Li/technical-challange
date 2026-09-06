"""TDD scenarios for the generic HTTP retry layer, organized by source type:

Type 1 = Source A (stable, no transient failures)
Type 2 = Source B (transient 502/503 before eventually succeeding, or failing
          permanently if the failure budget exceeds MAX_ATTEMPTS)
Type 3 = Source C (429 rate-limit, honoring Retry-After)

A `ScriptedTransport` test double replays a canned sequence of responses in
order, one per call, so these tests run without real network I/O or real
sleeps.
"""

import unittest
from unittest.mock import Mock

from aggregator.http_retry import (
    MAX_ATTEMPTS,
    HTTPError,
    HTTPResponse,
    RetryExhaustedError,
    TransportError,
    fetch_with_retry,
)


class ScriptedTransport:
    """Stand-in for a real HTTP client: replays a scripted sequence of
    responses/exceptions, one per call, in order."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def __call__(self) -> HTTPResponse:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(body=None) -> HTTPResponse:
    return HTTPResponse(status_code=200, body=body if body is not None else {"ok": True})


def fail(status_code: int, retry_after: float | None = None) -> HTTPResponse:
    headers = {"Retry-After": str(retry_after)
               } if retry_after is not None else {}
    return HTTPResponse(status_code=status_code, headers=headers)


class TestType1SourceA(unittest.TestCase):
    """Type 1 (Source A): stable, no failures expected."""

    def test_1_standard_success_request(self):
        """A normal 200 response is returned on the first attempt, no retries."""
        transport = ScriptedTransport([ok({"page": 1})])
        sleep_fn = Mock()
        response = fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 1)
        sleep_fn.assert_not_called()

    def test_2_fail_request_not_retried(self):
        """A non-retryable 4xx (e.g. bad request) fails immediately, with no retries burned."""
        transport = ScriptedTransport([fail(400)])
        sleep_fn = Mock()
        with self.assertRaises(HTTPError) as ctx:
            fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(ctx.exception.attempts, 1)
        sleep_fn.assert_not_called()


class TestType2SourceB(unittest.TestCase):
    """Type 2 (Source B): transient 502/503 failures."""

    def test_3_success_request(self):
        """A normal 200 response is returned on the first attempt, no retries."""
        transport = ScriptedTransport([ok({"items": []})])
        response = fetch_with_retry(transport, sleep_fn=Mock())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 1)

    def test_4_502_eventually_succeeds(self):
        """One 502 followed by a 200 succeeds after a single retry, honoring Retry-After."""
        transport = ScriptedTransport(
            [fail(502, retry_after=1), ok({"items": []})])
        sleep_fn = Mock()
        response = fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)
        sleep_fn.assert_called_once_with(1.0)

    def test_5_502_eventually_fails(self):
        """502s beyond MAX_ATTEMPTS exhaust the retry budget and raise HTTPError."""
        script = [fail(502, retry_after=1) for _ in range(MAX_ATTEMPTS + 1)]
        transport = ScriptedTransport(script)
        with self.assertRaises(HTTPError) as ctx:
            fetch_with_retry(transport, sleep_fn=Mock())
        self.assertEqual(transport.calls, MAX_ATTEMPTS)
        self.assertEqual(ctx.exception.response.status_code, 502)

    def test_6_503_eventually_succeeds(self):
        """One 503 followed by a 200 succeeds after a single retry, honoring Retry-After."""
        transport = ScriptedTransport(
            [fail(503, retry_after=1), ok({"items": []})])
        sleep_fn = Mock()
        response = fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)
        sleep_fn.assert_called_once_with(1.0)

    def test_7_503_eventually_fails(self):
        """503s beyond MAX_ATTEMPTS exhaust the retry budget and raise HTTPError."""
        script = [fail(503, retry_after=1) for _ in range(MAX_ATTEMPTS + 1)]
        transport = ScriptedTransport(script)
        with self.assertRaises(HTTPError) as ctx:
            fetch_with_retry(transport, sleep_fn=Mock())
        self.assertEqual(transport.calls, MAX_ATTEMPTS)
        self.assertEqual(ctx.exception.response.status_code, 503)

    def test_8_502_without_retry_after_uses_default_backoff_then_succeeds(self):
        """A 502 with no Retry-After header falls back to the default backoff, then succeeds."""
        transport = ScriptedTransport([fail(502), ok({"items": []})])
        sleep_fn = Mock()
        response = fetch_with_retry(
            transport, default_backoff=0.5, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)
        sleep_fn.assert_called_once_with(0.5)


class TestType3SourceC(unittest.TestCase):
    """Type 3 (Source C): 429 rate-limit, honoring Retry-After."""

    def test_9_429_eventually_fails(self):
        """429s beyond MAX_ATTEMPTS exhaust the retry budget and raise HTTPError."""
        script = [fail(429, retry_after=1) for _ in range(MAX_ATTEMPTS + 1)]
        transport = ScriptedTransport(script)
        with self.assertRaises(HTTPError) as ctx:
            fetch_with_retry(transport, sleep_fn=Mock())
        self.assertEqual(transport.calls, MAX_ATTEMPTS)
        self.assertEqual(ctx.exception.response.status_code, 429)

    def test_10_429_eventually_succeeds(self):
        """One 429 followed by a 200 succeeds after a single retry, honoring Retry-After."""
        transport = ScriptedTransport(
            [fail(429, retry_after=1), ok({"data": []})])
        sleep_fn = Mock()
        response = fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)
        sleep_fn.assert_called_once_with(1.0)


class TestMixedAndEdgeCases(unittest.TestCase):
    """Less common but real sequences: mixed error types, timeouts, and
    boundary conditions around MAX_ATTEMPTS."""

    def test_502_then_non_retryable_400_stops_immediately(self):
        """A retryable 502 followed by a non-retryable 400 stops retrying right away, even with attempts remaining."""
        transport = ScriptedTransport([fail(502, retry_after=1), fail(400)])
        sleep_fn = Mock()
        with self.assertRaises(HTTPError) as ctx:
            fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(transport.calls, 2)
        self.assertEqual(ctx.exception.response.status_code, 400)
        sleep_fn.assert_called_once_with(1.0)

    def test_502_then_429_then_succeeds(self):
        """Different retryable status codes can appear back-to-back and still lead to eventual success."""
        transport = ScriptedTransport(
            [fail(502, retry_after=1), fail(429, retry_after=1), ok({"items": []})]
        )
        sleep_fn = Mock()
        response = fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 3)
        self.assertEqual(sleep_fn.call_count, 2)

    def test_two_failures_then_success_at_max_attempts_boundary(self):
        """MAX_ATTEMPTS-1 failures followed by success on the last allowed attempt (Source B's cursor-3 pattern)."""
        transport = ScriptedTransport(
            [fail(502, retry_after=1), fail(502, retry_after=1), ok({"items": []})]
        )
        response = fetch_with_retry(transport, sleep_fn=Mock())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, MAX_ATTEMPTS)

    def test_timeout_then_succeeds(self):
        """A connection timeout is retried the same as a retryable status code, then succeeds."""
        transport = ScriptedTransport([TimeoutError(), ok({"items": []})])
        sleep_fn = Mock()
        response = fetch_with_retry(transport, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)
        sleep_fn.assert_called_once()

    def test_timeout_persists_and_eventually_raises(self):
        """Repeated timeouts beyond MAX_ATTEMPTS raise TransportError, wrapping the original TimeoutError so callers can catch one failure type regardless of cause."""
        transport = ScriptedTransport([TimeoutError() for _ in range(MAX_ATTEMPTS)])
        with self.assertRaises(TransportError) as ctx:
            fetch_with_retry(transport, sleep_fn=Mock())
        self.assertEqual(transport.calls, MAX_ATTEMPTS)
        self.assertIsInstance(ctx.exception.original_error, TimeoutError)
        self.assertIsInstance(ctx.exception, RetryExhaustedError)

    def test_http_error_is_also_a_retry_exhausted_error(self):
        """HTTPError and TransportError share a common base so callers can catch failures uniformly."""
        transport = ScriptedTransport([fail(400)])
        with self.assertRaises(RetryExhaustedError):
            fetch_with_retry(transport, sleep_fn=Mock())

    def test_malformed_retry_after_header_falls_back_to_default_backoff(self):
        """A non-numeric Retry-After value is ignored in favor of the default backoff."""
        transport = ScriptedTransport(
            [HTTPResponse(status_code=502, headers={"Retry-After": "not-a-number"}), ok({"items": []})]
        )
        sleep_fn = Mock()
        response = fetch_with_retry(transport, default_backoff=0.5, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        sleep_fn.assert_called_once_with(0.5)

    def test_negative_retry_after_falls_back_to_default_backoff(self):
        """A negative Retry-After (would crash a real sleep_fn) is treated as absent, falling back to the default backoff."""
        transport = ScriptedTransport(
            [HTTPResponse(status_code=429, headers={"Retry-After": "-5"}), ok({"data": []})]
        )
        sleep_fn = Mock()
        response = fetch_with_retry(transport, default_backoff=0.5, sleep_fn=sleep_fn)
        self.assertEqual(response.status_code, 200)
        sleep_fn.assert_called_once_with(0.5)

    def test_max_attempts_below_one_raises_value_error(self):
        """max_attempts=0 is a misuse of the API and should fail clearly, not with a confusing AttributeError."""
        with self.assertRaises(ValueError):
            fetch_with_retry(ScriptedTransport([ok()]), max_attempts=0, sleep_fn=Mock())


if __name__ == "__main__":
    unittest.main()
