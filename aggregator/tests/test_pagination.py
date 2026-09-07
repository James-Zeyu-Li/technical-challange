"""TDD scenarios for the pagination layer (aggregator/pagination.py), not
yet implemented.

Each source paginates differently (page number / cursor / offset-limit),
but the loop itself should be one generic function - per-source
differences live in declarative config (how to read "is there a next
page" and "what's the next page's URL" from the previous response body),
the same pattern already used for normalize()'s field maps.

`fetch_page` is faked here (maps URL -> canned HTTPResponse) so these tests
never touch a real network or a real mock server.
"""

import unittest
from unittest.mock import Mock

from aggregator.http_retry import (
    DEFAULT_BACKOFF_SECONDS,
    HTTPResponse,
    RetryExhaustedError,
)
from aggregator.pagination import (
    MAX_PAGES,
    MalformedPaginationEnvelopeError,
    PaginationLimitExceededError,
    fetch_all_pages,
)

BASE_URL = "http://localhost:8080"


def fake_fetch_page(responses: dict):
    """A fetch_page stand-in that maps each exact URL to a canned HTTPResponse."""

    def fetch_page(url: str) -> HTTPResponse:
        return responses[url]

    return fetch_page


class TestSourceAPageNumberPagination(unittest.TestCase):
    """Source A: page-number pagination, stops when page >= total_pages."""

    def test_collects_all_records_across_three_pages(self):
        """All 3 pages are fetched in order and their records concatenated."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-a/products?page=1": HTTPResponse(
                    status_code=200,
                    body={"page": 1, "total_pages": 3, "products": [
                        {"id": "a-101"}, {"id": "a-102"}]},
                ),
                f"{BASE_URL}/source-a/products?page=2": HTTPResponse(
                    status_code=200,
                    body={"page": 2, "total_pages": 3, "products": [
                        {"id": "a-103"}, {"id": "a-104"}]},
                ),
                f"{BASE_URL}/source-a/products?page=3": HTTPResponse(
                    status_code=200,
                    body={"page": 3, "total_pages": 3, "products": [
                        {"id": "a-105"}, {"id": "a-106"}]},
                ),
            }
        )
        records = fetch_all_pages(BASE_URL, "source_a", fetch_page)
        self.assertEqual([r["id"] for r in records], [
                         "a-101", "a-102", "a-103", "a-104", "a-105", "a-106"])

    def test_single_page_source_stops_after_one_request(self):
        """When total_pages == 1, the loop makes exactly one request and stops."""
        calls = []

        def fetch_page(url: str) -> HTTPResponse:
            calls.append(url)
            return HTTPResponse(status_code=200, body={"page": 1, "total_pages": 1, "products": [{"id": "a-1"}]})

        records = fetch_all_pages(BASE_URL, "source_a", fetch_page)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r["id"] for r in records], ["a-1"])


class TestSourceBCursorPagination(unittest.TestCase):
    """Source B: opaque-cursor pagination, stops when next_cursor is null."""

    def test_collects_all_records_across_cursor_chain(self):
        """The first request has no cursor; each response's next_cursor drives the following request, until next_cursor is null."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-b/products": HTTPResponse(
                    status_code=200,
                    body={"items": [{"sku": "b-201"},
                                    {"sku": "b-202"}], "next_cursor": "cursor-2"},
                ),
                f"{BASE_URL}/source-b/products?cursor=cursor-2": HTTPResponse(
                    status_code=200,
                    body={"items": [{"sku": "b-203"},
                                    {"sku": "b-204"}], "next_cursor": "cursor-3"},
                ),
                f"{BASE_URL}/source-b/products?cursor=cursor-3": HTTPResponse(
                    status_code=200,
                    body={"items": [{"sku": "b-205"},
                                    {"sku": "b-206"}], "next_cursor": None},
                ),
            }
        )
        records = fetch_all_pages(BASE_URL, "source_b", fetch_page)
        self.assertEqual(
            [r["sku"] for r in records], [
                "b-201", "b-202", "b-203", "b-204", "b-205", "b-206"]
        )


class TestSourceCOffsetPagination(unittest.TestCase):
    """Source C: offset/limit pagination, stops when next_offset is null."""

    def test_collects_all_records_across_offset_chain(self):
        """Each response's next_offset drives the following request, until next_offset is null."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-c/products?offset=0&limit=2": HTTPResponse(
                    status_code=200,
                    body={"data": [{"product_id": "c-301"},
                                   {"product_id": "c-302"}], "next_offset": 2},
                ),
                f"{BASE_URL}/source-c/products?offset=2&limit=2": HTTPResponse(
                    status_code=200,
                    body={"data": [{"product_id": "c-303"},
                                   {"product_id": "c-304"}], "next_offset": 4},
                ),
                f"{BASE_URL}/source-c/products?offset=4&limit=2": HTTPResponse(
                    status_code=200,
                    body={"data": [{"product_id": "c-305"},
                                   {"product_id": "c-306"}], "next_offset": None},
                ),
            }
        )
        # Source C is throttled by default; use a no-op sleep_fn so this
        # test doesn't really wait on the real min-interval delay.
        records = fetch_all_pages(BASE_URL, "source_c", fetch_page, sleep_fn=Mock())
        self.assertEqual(
            [r["product_id"] for r in records], [
                "c-301", "c-302", "c-303", "c-304", "c-305", "c-306"]
        )


class TestPaginationFailurePropagation(unittest.TestCase):
    """Pagination itself doesn't decide what a source-level failure means -
    that's the orchestrator's job (isolating one source from the others).
    A failure partway through a page sequence must propagate, not be
    swallowed here."""

    def test_retry_exhausted_on_a_later_page_propagates(self):
        """A failure on page 2 (not page 1) still raises RetryExhaustedError out of fetch_all_pages, it isn't caught/hidden here."""

        def fetch_page(url: str) -> HTTPResponse:
            if url == f"{BASE_URL}/source-a/products?page=1":
                return HTTPResponse(status_code=200, body={"page": 1, "total_pages": 3, "products": [{"id": "a-1"}]})
            return HTTPResponse(status_code=503, headers={"Retry-After": "0"})

        with self.assertRaises(RetryExhaustedError):
            fetch_all_pages(BASE_URL, "source_a", fetch_page)


class TestPaginationSafetyLimit(unittest.TestCase):
    """Defensive cap against a source whose pagination metadata never
    signals 'done' (e.g. a buggy or cyclic next_cursor) - protects against
    an infinite loop rather than assuming well-behaved sources."""

    def test_exceeding_max_pages_raises_pagination_limit_exceeded_error(self):
        """A source that always claims 'there's more' is stopped after MAX_PAGES requests instead of looping forever."""
        call_count = 0

        def fetch_page(url: str) -> HTTPResponse:
            nonlocal call_count
            call_count += 1
            return HTTPResponse(
                status_code=200,
                body={"items": [{"sku": f"b-{call_count}"}],
                      "next_cursor": "always-more"},
            )

        with self.assertRaises(PaginationLimitExceededError):
            fetch_all_pages(BASE_URL, "source_b", fetch_page)
        self.assertEqual(call_count, MAX_PAGES)


class TestEmptyFirstPage(unittest.TestCase):
    """A source with genuinely zero data must return an empty list, not
    error out - "no records" is a valid outcome, not a failure."""

    def test_source_a_empty_single_page_returns_no_records(self):
        """total_pages == 1 with an empty products list yields an empty result with exactly one request."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-a/products?page=1": HTTPResponse(
                    status_code=200, body={"page": 1, "total_pages": 1, "products": []}
                )
            }
        )
        records = fetch_all_pages(BASE_URL, "source_a", fetch_page)
        self.assertEqual(records, [])

    def test_source_b_empty_first_page_with_null_cursor_returns_no_records(self):
        """next_cursor is null on the very first response (no follow-up request) with no items -> empty result."""
        fetch_page = fake_fetch_page(
            {f"{BASE_URL}/source-b/products": HTTPResponse(
                status_code=200, body={"items": [], "next_cursor": None})}
        )
        records = fetch_all_pages(BASE_URL, "source_b", fetch_page)
        self.assertEqual(records, [])

    def test_source_c_empty_first_page_with_null_offset_returns_no_records(self):
        """next_offset is null on the very first response with no data -> empty result."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-c/products?offset=0&limit=2": HTTPResponse(
                    status_code=200, body={"data": [], "next_offset": None}
                )
            }
        )
        records = fetch_all_pages(BASE_URL, "source_c", fetch_page)
        self.assertEqual(records, [])


class TestMalformedPaginationEnvelope(unittest.TestCase):
    """A page's pagination-control fields (not its records) can themselves
    be missing or malformed. This is distinct from a bad *record*: it means
    we can no longer safely tell whether more pages exist, so it's raised
    as a source-level failure (like RetryExhaustedError) instead of being
    silently skipped or guessed at."""

    def test_source_a_missing_total_pages_key_raises(self):
        """total_pages is absent entirely (not just null) - can't tell if there's a next page."""
        fetch_page = fake_fetch_page(
            {f"{BASE_URL}/source-a/products?page=1": HTTPResponse(
                status_code=200, body={"page": 1, "products": []})}
        )
        with self.assertRaises(MalformedPaginationEnvelopeError):
            fetch_all_pages(BASE_URL, "source_a", fetch_page)

    def test_source_a_non_integer_total_pages_raises(self):
        """total_pages present but the wrong type (e.g. a string) is also malformed, not silently coerced."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-a/products?page=1": HTTPResponse(
                    status_code=200, body={"page": 1, "total_pages": "three", "products": []}
                )
            }
        )
        with self.assertRaises(MalformedPaginationEnvelopeError):
            fetch_all_pages(BASE_URL, "source_a", fetch_page)

    def test_source_b_missing_next_cursor_key_raises(self):
        """next_cursor key is absent entirely - distinct from being present-but-null (which legitimately means 'done')."""
        fetch_page = fake_fetch_page(
            {f"{BASE_URL}/source-b/products": HTTPResponse(
                status_code=200, body={"items": []})}
        )
        with self.assertRaises(MalformedPaginationEnvelopeError):
            fetch_all_pages(BASE_URL, "source_b", fetch_page)

    def test_source_b_null_next_cursor_is_not_an_error(self):
        """A present-but-null next_cursor is the valid 'done' signal, not malformed - guards against over-rejecting the well-formed case."""
        fetch_page = fake_fetch_page(
            {f"{BASE_URL}/source-b/products": HTTPResponse(
                status_code=200, body={"items": [], "next_cursor": None})}
        )
        records = fetch_all_pages(BASE_URL, "source_b", fetch_page)
        self.assertEqual(records, [])

    def test_source_c_missing_next_offset_key_raises(self):
        """next_offset key is absent entirely on Source C."""
        fetch_page = fake_fetch_page(
            {f"{BASE_URL}/source-c/products?offset=0&limit=2":
                HTTPResponse(status_code=200, body={"data": []})}
        )
        with self.assertRaises(MalformedPaginationEnvelopeError):
            fetch_all_pages(BASE_URL, "source_c", fetch_page)

    def test_source_missing_records_key_raises(self):
        """The records list itself (e.g. 'items' for Source B) being absent is also an envelope problem, not an empty-data case."""
        fetch_page = fake_fetch_page(
            {f"{BASE_URL}/source-b/products": HTTPResponse(
                status_code=200, body={"next_cursor": None})}
        )
        with self.assertRaises(MalformedPaginationEnvelopeError):
            fetch_all_pages(BASE_URL, "source_b", fetch_page)


class TestSourceCThrottling(unittest.TestCase):
    """Source C has a real rate limit (2 requests/second). Pagination
    proactively spaces its own requests instead of relying only on reactive
    429-retry, since 3 sequential page requests would otherwise likely
    trigger 429 in a real run."""

    def test_source_c_sleeps_between_pages_when_no_time_has_elapsed(self):
        """With zero real elapsed time between requests, each page after the first waits the full min interval."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-c/products?offset=0&limit=2": HTTPResponse(
                    status_code=200, body={"data": [{"product_id": "c-1"}], "next_offset": 2}
                ),
                f"{BASE_URL}/source-c/products?offset=2&limit=2": HTTPResponse(
                    status_code=200, body={"data": [{"product_id": "c-2"}], "next_offset": 4}
                ),
                f"{BASE_URL}/source-c/products?offset=4&limit=2": HTTPResponse(
                    status_code=200, body={"data": [{"product_id": "c-3"}], "next_offset": None}
                ),
            }
        )
        sleep_fn = Mock()
        # A clock that never advances - the worst case, where fetches are
        # instantaneous and throttling is the only thing spacing requests.
        now_fn = Mock(return_value=100.0)

        records = fetch_all_pages(BASE_URL, "source_c", fetch_page, sleep_fn=sleep_fn, now_fn=now_fn)

        self.assertEqual(len(records), 3)
        # No wait before the first request; one wait before each subsequent one.
        self.assertEqual(sleep_fn.call_count, 2)
        for call in sleep_fn.call_args_list:
            self.assertAlmostEqual(call.args[0], 0.6)

    def test_source_c_does_not_sleep_if_enough_time_already_elapsed(self):
        """If real fetch latency already exceeds the min interval, no extra (or negative) sleep is added."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-c/products?offset=0&limit=2": HTTPResponse(
                    status_code=200, body={"data": [{"product_id": "c-1"}], "next_offset": 2}
                ),
                f"{BASE_URL}/source-c/products?offset=2&limit=2": HTTPResponse(
                    status_code=200, body={"data": [{"product_id": "c-2"}], "next_offset": None}
                ),
            }
        )
        sleep_fn = Mock()
        # 10 seconds elapse between requests - way more than the min interval.
        now_fn = Mock(side_effect=[0.0, 10.0, 20.0])

        fetch_all_pages(BASE_URL, "source_c", fetch_page, sleep_fn=sleep_fn, now_fn=now_fn)
        sleep_fn.assert_not_called()

    def test_source_a_and_b_are_never_throttled(self):
        """Sources without a configured rate limit are never throttled, no matter how many pages."""
        fetch_page = fake_fetch_page(
            {
                f"{BASE_URL}/source-a/products?page=1": HTTPResponse(
                    status_code=200, body={"page": 1, "total_pages": 2, "products": [{"id": "a-1"}]}
                ),
                f"{BASE_URL}/source-a/products?page=2": HTTPResponse(
                    status_code=200, body={"page": 2, "total_pages": 2, "products": [{"id": "a-2"}]}
                ),
            }
        )
        sleep_fn = Mock()
        now_fn = Mock(return_value=100.0)

        fetch_all_pages(BASE_URL, "source_a", fetch_page, sleep_fn=sleep_fn, now_fn=now_fn)
        sleep_fn.assert_not_called()


class TestFetchAllPagesForwardsSleepFn(unittest.TestCase):
    """fetch_all_pages must thread its own sleep_fn into each page's
    fetch_with_retry call, not just use it for Source C's throttle wait -
    otherwise a retry's backoff sleep falls back to real time.sleep with no
    way to prevent it via this module's public API."""

    def test_retry_backoff_uses_the_injected_sleep_fn(self):
        """A retryable failure with no Retry-After header falls back to DEFAULT_BACKOFF_SECONDS via fetch_with_retry - that sleep must go through the sleep_fn passed into fetch_all_pages, not real time.sleep."""
        calls = []

        def fetch_page(url: str) -> HTTPResponse:
            calls.append(url)
            if len(calls) == 1:
                return HTTPResponse(status_code=502)  # no Retry-After header
            return HTTPResponse(status_code=200, body={"items": [{"sku": "b-1"}], "next_cursor": None})

        sleep_fn = Mock()
        records = fetch_all_pages(BASE_URL, "source_b", fetch_page, sleep_fn=sleep_fn)

        self.assertEqual([r["sku"] for r in records], ["b-1"])
        sleep_fn.assert_called_once_with(DEFAULT_BACKOFF_SECONDS)


if __name__ == "__main__":
    unittest.main()
