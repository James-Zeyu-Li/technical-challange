"""TDD scenarios for the real HTTP transport adapter (aggregator/transport.py).

This adapter is the only piece of the pipeline that touches real sockets.
It's a thin translation layer between `urllib` (stdlib, no third-party
dependency) and the interface `fetch_with_retry()` already expects:
returns an `HTTPResponse` on any response (success or HTTP error status),
or raises `TimeoutError`/`ConnectionError` on a network-level failure.

`urllib.request.urlopen` is mocked throughout - these tests never touch a
real socket or a real running mock server. That real-server check is done
manually instead (see PLAN.md's Testing and Verification Strategy).
"""

import io
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from aggregator.http_retry import HTTPResponse
from aggregator.transport import urllib_get


def make_urlopen_response(status: int, headers: dict, body: bytes):
    """Build a fake object matching what `with urlopen(...) as resp:` yields."""
    resp = MagicMock()
    resp.status = status
    resp.headers = headers
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestUrllibGetSuccess(unittest.TestCase):
    """A normal response (any 2xx) is parsed into an HTTPResponse."""

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_200_response_returns_parsed_json_body(self, mock_urlopen):
        """A 200 response's JSON body and headers are both surfaced on the returned HTTPResponse."""
        mock_urlopen.return_value = make_urlopen_response(
            200, {"Content-Type": "application/json"}, b'{"page": 1, "products": []}'
        )
        result = urllib_get("http://localhost:8080/source-a/products?page=1")
        self.assertIsInstance(result, HTTPResponse)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, {"page": 1, "products": []})


class TestUrllibGetHTTPErrorStatus(unittest.TestCase):
    """urllib raises HTTPError for 4xx/5xx instead of returning normally -
    the adapter must catch this and translate it into a normal HTTPResponse
    so fetch_with_retry can see the status code and decide whether to retry."""

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_502_is_translated_to_httpresponse_not_raised(self, mock_urlopen):
        """A 502 HTTPError is caught and turned into an HTTPResponse(status_code=502, ...) rather than propagating as an exception."""
        body = b'{"error": "transient_upstream_failure"}'
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:8080/source-b/products",
            code=502,
            msg="Bad Gateway",
            hdrs={"Retry-After": "1"},
            fp=io.BytesIO(body),
        )
        result = urllib_get("http://localhost:8080/source-b/products?cursor=cursor-2")
        self.assertIsInstance(result, HTTPResponse)
        self.assertEqual(result.status_code, 502)

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_retry_after_header_is_preserved_on_error_status(self, mock_urlopen):
        """The Retry-After header on an error response must survive translation, since fetch_with_retry reads it to time the next attempt."""
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:8080/source-c/products",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "2", "X-RateLimit-Limit": "2"},
            fp=io.BytesIO(b'{"error": "rate_limited"}'),
        )
        result = urllib_get("http://localhost:8080/source-c/products?offset=0&limit=2")
        self.assertEqual(result.headers.get("Retry-After"), "2")

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_400_is_also_translated_not_raised(self, mock_urlopen):
        """Non-retryable error statuses (e.g. 400) are translated the same way - retryability is fetch_with_retry's decision, not the transport's."""
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:8080/source-a/products",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(b'{"error": "bad_request"}'),
        )
        result = urllib_get("http://localhost:8080/source-a/products?page=abc")
        self.assertEqual(result.status_code, 400)


class TestUrllibGetNetworkFailures(unittest.TestCase):
    """Failures below the HTTP layer (no response at all) must surface as
    TimeoutError/ConnectionError so fetch_with_retry's existing except
    clause handles them without any changes to that module."""

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_connection_refused_raises_connection_error(self, mock_urlopen):
        """A URLError wrapping a connection failure (e.g. server not running) is translated to ConnectionError."""
        mock_urlopen.side_effect = urllib.error.URLError(ConnectionRefusedError("Connection refused"))
        with self.assertRaises(ConnectionError):
            urllib_get("http://localhost:8080/source-a/products?page=1")

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_socket_timeout_raises_timeout_error(self, mock_urlopen):
        """A socket-level timeout is surfaced as TimeoutError (in Python 3.10+, socket.timeout already is TimeoutError)."""
        mock_urlopen.side_effect = TimeoutError("timed out")
        with self.assertRaises(TimeoutError):
            urllib_get("http://localhost:8080/source-a/products?page=1", timeout=0.01)

    @patch("aggregator.transport.urllib.request.urlopen")
    def test_urlerror_wrapping_a_timeout_raises_timeout_error_not_connection_error(self, mock_urlopen):
        """urllib sometimes wraps a connect-phase timeout as URLError(reason=TimeoutError(...)) instead of raising TimeoutError directly. This must still surface as TimeoutError, not be misclassified as ConnectionError."""
        mock_urlopen.side_effect = urllib.error.URLError(TimeoutError("timed out"))
        with self.assertRaises(TimeoutError):
            urllib_get("http://localhost:8080/source-a/products?page=1", timeout=0.01)


if __name__ == "__main__":
    unittest.main()
