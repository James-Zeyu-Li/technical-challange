"""Tests for the minimal orchestrator that strings transport + retry +
normalize together. `fetch_page` is faked here (no real network, no real
mock server) - same test-double approach used for the other layers."""

import unittest
from unittest.mock import Mock

from aggregator.http_retry import DEFAULT_BACKOFF_SECONDS, HTTPResponse
from aggregator.orchestrator import run, run_source


def fake_fetch_page(responses: dict) -> callable:
    """Returns a fetch_page stand-in that maps each URL to a canned HTTPResponse."""

    def fetch_page(url: str) -> HTTPResponse:
        return responses[url]

    return fetch_page


class TestRunSource(unittest.TestCase):
    def test_successful_page_returns_normalized_records(self):
        """A normal first-page response is fully normalized into the unified schema."""
        fetch_page = fake_fetch_page(
            {
                "http://localhost:8080/source-a/products?page=1": HTTPResponse(
                    status_code=200,
                    body={
                        "page": 1,
                        "total_pages": 1,
                        "products": [
                            {"id": "a-101", "name": "Mechanical Keyboard",
                                "price": 89.99, "category": "electronics"}
                        ],
                    },
                )
            }
        )
        result = run_source("http://localhost:8080", "source_a", fetch_page)
        self.assertIsNone(result.error)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(
            result.records,
            [{"source": "source_a", "id": "a-101", "title": "Mechanical Keyboard",
                "price": 89.99, "category": "electronics"}],
        )

    def test_malformed_record_is_skipped_and_counted_without_failing_the_page(self):
        """A malformed record (Source B's known bad price) is skipped and counted; the well-formed record next to it still comes through."""
        fetch_page = fake_fetch_page(
            {
                "http://localhost:8080/source-b/products": HTTPResponse(
                    status_code=200,
                    body={
                        "items": [
                            {"sku": "b-201", "title": "Desk Lamp",
                                "amount_cents": 3499, "department": "home"},
                            {"sku": "b-205", "title": "Broken Price Example",
                                "amount_cents": "not-a-number", "department": "home"},
                        ],
                        "next_cursor": None,
                    },
                )
            }
        )
        result = run_source("http://localhost:8080", "source_b", fetch_page)
        self.assertIsNone(result.error)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0]["id"], "b-201")

    def test_source_failure_is_reported_not_raised(self):
        """When the source never returns a usable response, run_source reports it as an error on the result instead of raising."""

        def always_fails(url: str) -> HTTPResponse:
            return HTTPResponse(status_code=503, headers={"Retry-After": "0"})

        result = run_source("http://localhost:8080", "source_b", always_fails)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.records, [])
        self.assertEqual(result.skipped, 0)


class TestRun(unittest.TestCase):
    def test_one_source_failing_does_not_stop_the_others(self):
        """Source-level isolation: Source B failing entirely still lets Source A and C's results come through, and the run is reported as partial_success."""

        def fetch_page(url: str) -> HTTPResponse:
            if "source-a" in url:
                return HTTPResponse(
                    status_code=200,
                    body={"page": 1, "total_pages": 1, "products": [
                        {"id": "a-101", "name": "Keyboard", "price": 89.99, "category": "electronics"}]},
                )
            if "source-b" in url:
                return HTTPResponse(status_code=503, headers={"Retry-After": "0"})
            return HTTPResponse(
                status_code=200,
                body={"data": [{"product_id": "c-301", "product_name": "USB-C Hub",
                                "price": "49.50", "type": "electronics"}], "next_offset": None},
            )

        summary = run("http://localhost:8080", fetch_page)
        self.assertEqual(summary.status, "partial_success")

        by_source = {r.source: r for r in summary.results}
        self.assertIsNone(by_source["source_a"].error)
        self.assertEqual(len(by_source["source_a"].records), 1)
        self.assertIsNotNone(by_source["source_b"].error)
        self.assertIsNone(by_source["source_c"].error)
        self.assertEqual(len(by_source["source_c"].records), 1)

    def test_all_sources_succeeding_reports_success(self):
        """When every source returns usable data, the overall run status is 'success'."""

        def fetch_page(url: str) -> HTTPResponse:
            if "source-a" in url:
                body = {"page": 1, "total_pages": 1, "products": [
                    {"id": "a-1", "name": "X", "price": 1.0, "category": "c"}]}
            elif "source-b" in url:
                body = {"items": [
                    {"sku": "b-1", "title": "Y", "amount_cents": 100, "department": "d"}], "next_cursor": None}
            else:
                body = {"data": [{"product_id": "c-1", "product_name": "Z",
                                  "price": "1.00", "type": "e"}], "next_offset": None}
            return HTTPResponse(status_code=200, body=body)

        summary = run("http://localhost:8080", fetch_page)
        self.assertEqual(summary.status, "success")

    def test_all_sources_failing_reports_failed(self):
        """When every source fails, the overall run status is 'failed'."""

        def always_fails(url: str) -> HTTPResponse:
            return HTTPResponse(status_code=503, headers={"Retry-After": "0"})

        summary = run("http://localhost:8080", always_fails)
        self.assertEqual(summary.status, "failed")


class TestOrchestratorForwardsSleepFn(unittest.TestCase):
    """run_source/run must accept and forward sleep_fn/now_fn down into
    fetch_all_pages, not just default to real time.sleep - otherwise a
    retry's backoff sleep is unreachable through the orchestrator's public
    interface, the same gap fixed in fetch_all_pages itself."""

    def test_run_source_forwards_sleep_fn_to_retry_backoff(self):
        """A retryable failure with no Retry-After header must sleep via the sleep_fn passed into run_source, not real time.sleep."""
        calls = []

        def fetch_page(url: str) -> HTTPResponse:
            calls.append(url)
            if len(calls) == 1:
                return HTTPResponse(status_code=502)
            return HTTPResponse(status_code=200, body={"items": [{"sku": "b-1"}], "next_cursor": None})

        sleep_fn = Mock()
        result = run_source("http://localhost:8080",
                            "source_b", fetch_page, sleep_fn=sleep_fn)

        self.assertIsNone(result.error)
        sleep_fn.assert_called_once_with(DEFAULT_BACKOFF_SECONDS)


class TestDuplicateHandling(unittest.TestCase):
    """Per SPEC.md: duplicate id+source pairs within a single source keep
    the first-seen record and count the rest as duplicates, rather than
    silently overwriting or accumulating repeats."""

    def test_duplicate_id_within_a_source_keeps_first_and_counts_rest(self):
        """Two records sharing the same id on the same source: the first-seen one is kept, the second is counted as a duplicate, not appended."""
        fetch_page = fake_fetch_page(
            {
                "http://localhost:8080/source-a/products?page=1": HTTPResponse(
                    status_code=200,
                    body={
                        "page": 1,
                        "total_pages": 1,
                        "products": [
                            {"id": "a-101", "name": "First Seen", "price": 10.0, "category": "electronics"},
                            {"id": "a-101", "name": "Duplicate", "price": 99.0, "category": "electronics"},
                        ],
                    },
                )
            }
        )
        result = run_source("http://localhost:8080", "source_a", fetch_page)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0]["title"], "First Seen")
        self.assertEqual(result.duplicates, 1)

    def test_no_duplicates_when_all_ids_are_unique(self):
        """Distinct ids on the same source are all kept; duplicates stays at 0."""
        fetch_page = fake_fetch_page(
            {
                "http://localhost:8080/source-a/products?page=1": HTTPResponse(
                    status_code=200,
                    body={
                        "page": 1,
                        "total_pages": 1,
                        "products": [
                            {"id": "a-101", "name": "One", "price": 10.0, "category": "electronics"},
                            {"id": "a-102", "name": "Two", "price": 20.0, "category": "electronics"},
                        ],
                    },
                )
            }
        )
        result = run_source("http://localhost:8080", "source_a", fetch_page)
        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.duplicates, 0)


class TestRunLevelObservability(unittest.TestCase):
    """Per task.md: 'produce useful run-level summary information' and
    'provide enough observability to understand failures and performance'.
    elapsed_seconds and waits are recorded per source; RunSummary exposes
    cross-source totals."""

    def test_elapsed_seconds_is_recorded_even_on_success(self):
        """A successful source records how long its fetch+normalize took, using the injected now_fn (not real wall-clock time)."""
        fetch_page = fake_fetch_page(
            {
                "http://localhost:8080/source-a/products?page=1": HTTPResponse(
                    status_code=200,
                    body={"page": 1, "total_pages": 1, "products": [{"id": "a-1", "name": "X", "price": 1.0, "category": "c"}]},
                )
            }
        )
        # 3 calls: started_at, RateLimiter.record_request()'s timestamp, elapsed_seconds.
        now_fn = Mock(side_effect=[100.0, 101.0, 103.5])
        result = run_source("http://localhost:8080", "source_a", fetch_page, now_fn=now_fn)
        self.assertAlmostEqual(result.elapsed_seconds, 3.5)

    def test_elapsed_seconds_is_recorded_on_failure_too(self):
        """A source that ultimately fails still reports how long was spent before giving up."""

        def always_fails(url: str) -> HTTPResponse:
            return HTTPResponse(status_code=503, headers={"Retry-After": "0"})

        now_fn = Mock(side_effect=[200.0, 201.0])
        result = run_source("http://localhost:8080", "source_b", always_fails, now_fn=now_fn)
        self.assertIsNotNone(result.error)
        self.assertAlmostEqual(result.elapsed_seconds, 1.0)

    def test_waits_counts_retry_backoff_events(self):
        """Each time the retry engine backs off (a transient failure), that's one recorded wait - visible without reading logs."""
        calls = []

        def fetch_page(url: str) -> HTTPResponse:
            calls.append(url)
            if len(calls) <= 2:
                return HTTPResponse(status_code=502, headers={"Retry-After": "0"})
            return HTTPResponse(status_code=200, body={"items": [{"sku": "b-1"}], "next_cursor": None})

        result = run_source("http://localhost:8080", "source_b", fetch_page)
        self.assertIsNone(result.error)
        self.assertEqual(result.waits, 2)

    def test_waits_is_zero_when_nothing_needed_to_wait(self):
        """A clean single successful request records zero waits."""
        fetch_page = fake_fetch_page(
            {
                "http://localhost:8080/source-a/products?page=1": HTTPResponse(
                    status_code=200,
                    body={"page": 1, "total_pages": 1, "products": [{"id": "a-1", "name": "X", "price": 1.0, "category": "c"}]},
                )
            }
        )
        result = run_source("http://localhost:8080", "source_a", fetch_page)
        self.assertEqual(result.waits, 0)

    def test_run_summary_exposes_cross_source_totals(self):
        """RunSummary aggregates records/skipped/duplicates across all sources, not just per-source results."""

        def fetch_page(url: str) -> HTTPResponse:
            if "source-a" in url:
                body = {"page": 1, "total_pages": 1, "products": [{"id": "a-1", "name": "X", "price": 1.0, "category": "c"}]}
            elif "source-b" in url:
                body = {
                    "items": [
                        {"sku": "b-1", "title": "Y", "amount_cents": 100, "department": "d"},
                        {"sku": "b-2", "title": "Bad", "amount_cents": "oops", "department": "d"},
                    ],
                    "next_cursor": None,
                }
            else:
                body = {"data": [{"product_id": "c-1", "product_name": "Z", "price": "1.00", "type": "e"}], "next_offset": None}
            return HTTPResponse(status_code=200, body=body)

        summary = run("http://localhost:8080", fetch_page)
        self.assertEqual(summary.total_records, 3)
        self.assertEqual(summary.total_skipped, 1)
        self.assertEqual(summary.total_duplicates, 0)


class TestOverallRunDeadline(unittest.TestCase):
    """An optional max_seconds bounds the whole run's wall-clock time,
    complementing the existing per-request timeout in transport.py."""

    def test_run_source_reports_deadline_exceeded_as_error_not_raised(self):
        """A source whose deadline has already passed reports it as an error on the result, the same way other source-level failures are handled."""

        def fetch_page(url: str) -> HTTPResponse:
            raise AssertionError("should not be called once the deadline has passed")

        now_fn = Mock(return_value=100.0)
        result = run_source("http://localhost:8080", "source_a", fetch_page, now_fn=now_fn, deadline=99.0)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.records, [])

    def test_run_stops_starting_new_sources_once_deadline_passed(self):
        """Once the overall deadline has passed, sources that haven't started yet are skipped rather than attempted."""

        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def __call__(self) -> float:
                return self.now

        clock = FakeClock()

        def fetch_page(url: str) -> HTTPResponse:
            if "source-a" in url:
                # Simulate source_a alone taking longer than the whole run's budget.
                clock.now += 10.0
                return HTTPResponse(status_code=200, body={"page": 1, "total_pages": 1, "products": [{"id": "a-1"}]})
            raise AssertionError("should not be called: deadline already passed by the time this source starts")

        summary = run("http://localhost:8080", fetch_page, now_fn=clock, max_seconds=1.0)

        self.assertEqual(summary.status, "partial_success")
        by_source = {r.source: r for r in summary.results}
        self.assertIsNone(by_source["source_a"].error)
        self.assertIsNotNone(by_source["source_b"].error)
        self.assertIsNotNone(by_source["source_c"].error)


if __name__ == "__main__":
    unittest.main()
