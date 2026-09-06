"""Minimal orchestrator: strings transport + retry + normalize together.

This is a first-pass "main" that proves the three already-working layers
compose correctly. It deliberately fetches only the first page of each
source (no pagination loop yet - that's a separate, not-yet-built layer),
so it's the smallest version that can run end-to-end against the mock
service and demonstrate partial-success / malformed-record handling.

`fetch_page` is injected so this can be unit-tested without a real server,
the same pattern used in the transport and retry layers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from aggregator.http_retry import HTTPResponse, RetryExhaustedError, fetch_with_retry
from aggregator.normalize import MalformedRecordError, normalize
from aggregator.sources import FIELD_MAPS
from aggregator.transport import urllib_get

# Where to find each source's first page and its list of raw records in the
# response body. Pagination beyond page 1 is not implemented yet.
SOURCE_ENDPOINTS = {
    "source_a": {"path": "/source-a/products?page=1", "records_key": "products"},
    "source_b": {"path": "/source-b/products", "records_key": "items"},
    "source_c": {"path": "/source-c/products?offset=0&limit=2", "records_key": "data"},
}


@dataclass
class SourceResult:
    source: str
    records: list[dict] = field(default_factory=list)
    skipped: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    results: list[SourceResult]

    @property
    def status(self) -> str:
        succeeded = [r for r in self.results if r.error is None]
        if len(succeeded) == len(self.results):
            return "success"
        if succeeded:
            return "partial_success"
        return "failed"


def run_source(
    base_url: str,
    source_name: str,
    fetch_page: Callable[[str], HTTPResponse] = urllib_get,
) -> SourceResult:
    """Fetch and normalize one source's first page.

    A source-level failure (retries exhausted) is caught here and reported
    on the result rather than raised, so one source failing never stops the
    others (see SPEC.md's partial-success requirement).
    """
    endpoint = SOURCE_ENDPOINTS[source_name]
    url = f"{base_url}{endpoint['path']}"
    field_map = FIELD_MAPS[source_name]

    try:
        response = fetch_with_retry(lambda: fetch_page(url))
    except RetryExhaustedError as exc:
        return SourceResult(source=source_name, error=str(exc))

    raw_records = response.body.get(endpoint["records_key"], [])
    result = SourceResult(source=source_name)
    for raw in raw_records:
        try:
            result.records.append(normalize(raw, source_name, field_map))
        except MalformedRecordError:
            result.skipped += 1
    return result


def run(
    base_url: str = "http://localhost:8080",
    fetch_page: Callable[[str], HTTPResponse] = urllib_get,
) -> RunSummary:
    results = [run_source(base_url, source_name, fetch_page) for source_name in FIELD_MAPS]
    return RunSummary(results=results)


def main() -> None:
    summary = run()
    for result in summary.results:
        if result.error is not None:
            print(f"{result.source}: FAILED - {result.error}")
        else:
            print(f"{result.source}: {len(result.records)} records, {result.skipped} skipped")
    print(f"Overall: {summary.status}")


if __name__ == "__main__":
    main()
