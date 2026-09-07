"""Generic pagination loop shared by all three sources; per-source behavior lives in PAGINATION_CONFIG."""

from __future__ import annotations

import time
from collections.abc import Callable

from aggregator.http_retry import HTTPResponse, fetch_with_retry
from aggregator.rate_limiter import RateLimiter
from aggregator.transport import urllib_get

MAX_PAGES = 10


class PaginationLimitExceededError(Exception):
    """Raised when a source never signals 'done' within MAX_PAGES requests -
    a defensive cap against an infinite loop, not an expected occurrence."""

    def __init__(self, source: str, max_pages: int) -> None:
        self.source = source
        self.max_pages = max_pages
        super().__init__(f"[{source}] pagination did not terminate after {max_pages} pages")


class MalformedPaginationEnvelopeError(Exception):
    """Raised when a page's pagination-control fields are missing or the wrong type."""

    def __init__(self, source: str, body: dict, field: str, reason: str) -> None:
        self.source = source
        self.body = body
        self.field = field
        super().__init__(f"[{source}] malformed pagination envelope: field '{field}' {reason} (body={body!r})")


def _require_int(body: dict, field: str, source: str) -> int:
    if field not in body:
        raise MalformedPaginationEnvelopeError(source, body, field, "is missing")
    value = body[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedPaginationEnvelopeError(source, body, field, f"must be an int, got {type(value).__name__}")
    return value


def _source_a_next_url(base_url: str, source: str, body: dict) -> str | None:
    page = _require_int(body, "page", source)
    total_pages = _require_int(body, "total_pages", source)
    if page >= total_pages:
        return None
    return f"{base_url}/source-a/products?page={page + 1}"


def _source_b_next_url(base_url: str, source: str, body: dict) -> str | None:
    if "next_cursor" not in body:
        raise MalformedPaginationEnvelopeError(source, body, "next_cursor", "is missing")
    next_cursor = body["next_cursor"]
    if next_cursor is None:
        return None
    return f"{base_url}/source-b/products?cursor={next_cursor}"


def _source_c_next_url(base_url: str, source: str, body: dict) -> str | None:
    if "next_offset" not in body:
        raise MalformedPaginationEnvelopeError(source, body, "next_offset", "is missing")
    next_offset = body["next_offset"]
    if next_offset is None:
        return None
    return f"{base_url}/source-c/products?offset={next_offset}&limit=2"


# min_interval: default minimum seconds between requests to this source, or
# None for no proactive throttling. This is only a fallback - RateLimiter
# prefers the source's own X-RateLimit-Limit/X-RateLimit-Window response
# headers when present. Source C's fallback (0.6s) matches its documented
# 2-requests/second limit with margin for timing jitter.
PAGINATION_CONFIG = {
    "source_a": {
        "first_page_path": "/source-a/products?page=1",
        "records_key": "products",
        "next_url": _source_a_next_url,
        "min_interval": None,
    },
    "source_b": {
        "first_page_path": "/source-b/products",
        "records_key": "items",
        "next_url": _source_b_next_url,
        "min_interval": None,
    },
    "source_c": {
        "first_page_path": "/source-c/products?offset=0&limit=2",
        "records_key": "data",
        "next_url": _source_c_next_url,
        "min_interval": 0.6,
    },
}


def fetch_all_pages(
    base_url: str,
    source_name: str,
    fetch_page: Callable[[str], HTTPResponse] = urllib_get,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> list[dict]:
    config = PAGINATION_CONFIG[source_name]
    url: str | None = f"{base_url}{config['first_page_path']}"
    records_key = config["records_key"]
    next_url_fn = config["next_url"]
    rate_limiter = RateLimiter(config["min_interval"], now_fn=now_fn)

    all_records: list[dict] = []
    pages_fetched = 0
    while url is not None:
        if pages_fetched >= MAX_PAGES:
            raise PaginationLimitExceededError(source_name, MAX_PAGES)

        rate_limiter.wait_if_needed(sleep_fn)

        response = fetch_with_retry(lambda u=url: fetch_page(u), sleep_fn=sleep_fn)
        rate_limiter.record_request(response.headers)
        pages_fetched += 1
        body = response.body

        if records_key not in body:
            raise MalformedPaginationEnvelopeError(source_name, body, records_key, "is missing")
        all_records.extend(body[records_key])

        url = next_url_fn(base_url, source_name, body)

    return all_records
