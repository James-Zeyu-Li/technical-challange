"""Thin urllib-based HTTP transport adapter (stdlib only, no third-party deps).

The only job here is translation: `urllib` raises `HTTPError` for 4xx/5xx
responses and `URLError` for network-level failures, but `fetch_with_retry()`
expects either a normal `HTTPResponse` (any status code) or a raised
`TimeoutError`/`ConnectionError`. This module does that translation and
nothing else - it doesn't decide what's retryable, that's fetch_with_retry's
job.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from aggregator.http_retry import HTTPResponse

DEFAULT_TIMEOUT_SECONDS = 5.0


def urllib_get(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> HTTPResponse:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return _build_response(resp.status, resp.headers, resp.read())
    except urllib.error.HTTPError as exc:
        # An error status still counts as "got a response" to fetch_with_retry -
        # it must see the status code to decide whether to retry.
        return _build_response(exc.code, exc.headers, exc.read())
    except urllib.error.URLError as exc:
        # urllib sometimes wraps a connect-phase timeout as
        # URLError(reason=TimeoutError(...)) instead of raising TimeoutError
        # directly - don't let that get misclassified as ConnectionError.
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError(str(exc.reason)) from exc
        raise ConnectionError(str(exc.reason)) from exc


def _build_response(status_code: int, headers: Any, raw_body: bytes) -> HTTPResponse:
    body = json.loads(raw_body) if raw_body else None
    return HTTPResponse(status_code=status_code, headers=dict(headers.items()), body=body)
