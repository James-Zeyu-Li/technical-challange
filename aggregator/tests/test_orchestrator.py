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


if __name__ == "__main__":
    unittest.main()
