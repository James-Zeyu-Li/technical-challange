"""Orchestrator: runs transport + retry + pagination + normalize per source, isolating failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from aggregator.http_retry import HTTPResponse, RetryExhaustedError
from aggregator.normalize import MalformedRecordError, normalize
from aggregator.pagination import MalformedPaginationEnvelopeError, fetch_all_pages
from aggregator.sources import FIELD_MAPS
from aggregator.transport import urllib_get


@dataclass
class SourceResult:
    source: str
    records: list[dict] = field(default_factory=list)
    skipped: int = 0
    duplicates: int = 0
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
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> SourceResult:
    """Fetch every page and normalize every record for one source."""
    field_map = FIELD_MAPS[source_name]

    try:
        raw_records = fetch_all_pages(base_url, source_name, fetch_page, sleep_fn=sleep_fn, now_fn=now_fn)
    except (RetryExhaustedError, MalformedPaginationEnvelopeError) as exc:
        return SourceResult(source=source_name, error=str(exc))

    result = SourceResult(source=source_name)
    seen_ids: set[str] = set()
    for raw in raw_records:
        try:
            normalized = normalize(raw, source_name, field_map)
        except MalformedRecordError:
            result.skipped += 1
            continue

        if normalized["id"] in seen_ids:
            result.duplicates += 1
            continue
        seen_ids.add(normalized["id"])
        result.records.append(normalized)
    return result


def run(
    base_url: str = "http://localhost:8080",
    fetch_page: Callable[[str], HTTPResponse] = urllib_get,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> RunSummary:
    results = [
        run_source(base_url, source_name, fetch_page, sleep_fn, now_fn) for source_name in FIELD_MAPS
    ]
    return RunSummary(results=results)


def main() -> None:
    summary = run()
    for result in summary.results:
        if result.error is not None:
            print(f"{result.source}: FAILED - {result.error}")
        else:
            print(
                f"{result.source}: {len(result.records)} records, "
                f"{result.skipped} skipped, {result.duplicates} duplicates"
            )
    print(f"Overall: {summary.status}")


if __name__ == "__main__":
    main()
