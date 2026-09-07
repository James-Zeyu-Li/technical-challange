"""Orchestrator: runs transport + retry + pagination + normalize per source, isolating failures."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from aggregator.http_retry import HTTPResponse, RetryExhaustedError
from aggregator.normalize import MalformedRecordError, normalize
from aggregator.pagination import (
    DeadlineExceededError,
    MalformedPaginationEnvelopeError,
    fetch_all_pages,
)
from aggregator.sources import FIELD_MAPS
from aggregator.transport import urllib_get

logger = logging.getLogger(__name__)


@dataclass
class SourceResult:
    """Outcome of fetching and normalizing one source."""

    source: str
    records: list[dict] = field(default_factory=list)
    skipped: int = 0
    duplicates: int = 0
    error: str | None = None
    elapsed_seconds: float = 0.0
    # Combined count of retry-backoff waits and Source C's proactive
    # throttle waits - not split apart, since separating them would require
    # changing fetch_all_pages()'s return contract. See SPEC.md.
    waits: int = 0


@dataclass
class RunSummary:
    """Results for all sources from one run, plus cross-source totals."""

    results: list[SourceResult]
    # Real wall-clock time for the whole run, measured directly around the
    # concurrent fetch. Not the same as total_source_seconds, since sources
    # run in parallel and their individual times overlap.
    wall_clock_seconds: float = 0.0

    @property
    def status(self) -> str:
        succeeded = [r for r in self.results if r.error is None]
        if len(succeeded) == len(self.results):
            return "success"
        if succeeded:
            return "partial_success"
        return "failed"

    @property
    def total_records(self) -> int:
        return sum(len(r.records) for r in self.results)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped for r in self.results)

    @property
    def total_duplicates(self) -> int:
        return sum(r.duplicates for r in self.results)

    @property
    def total_source_seconds(self) -> float:
        """Sum of each source's own elapsed time. Since sources run
        concurrently, this overcounts real time and should not be read as
        the run's duration - see wall_clock_seconds for that."""
        return sum(r.elapsed_seconds for r in self.results)


def run_source(
    base_url: str,
    source_name: str,
    fetch_page: Callable[[str], HTTPResponse] = urllib_get,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> SourceResult:
    """Fetch every page and normalize every record for one source."""
    field_map = FIELD_MAPS[source_name]

    waits = 0

    def counting_sleep_fn(seconds: float) -> None:
        nonlocal waits
        waits += 1
        sleep_fn(seconds)

    started_at = now_fn()
    try:
        raw_records = fetch_all_pages(
            base_url, source_name, fetch_page, sleep_fn=counting_sleep_fn, now_fn=now_fn, deadline=deadline
        )
    except (RetryExhaustedError, MalformedPaginationEnvelopeError, DeadlineExceededError) as exc:
        logger.error("%s: giving up after %d wait(s): %s", source_name, waits, exc)
        return SourceResult(source=source_name, error=str(exc), elapsed_seconds=now_fn() - started_at, waits=waits)

    result = SourceResult(source=source_name)
    seen_ids: set[str] = set()
    for raw in raw_records:
        try:
            normalized = normalize(raw, source_name, field_map)
        except MalformedRecordError as exc:
            logger.warning("%s: skipping malformed record: %s", source_name, exc)
            result.skipped += 1
            continue

        if normalized["id"] in seen_ids:
            logger.warning("%s: skipping duplicate id %r", source_name, normalized["id"])
            result.duplicates += 1
            continue
        seen_ids.add(normalized["id"])
        result.records.append(normalized)

    result.elapsed_seconds = now_fn() - started_at
    result.waits = waits
    return result


def run(
    base_url: str = "http://localhost:8080",
    fetch_page: Callable[[str], HTTPResponse] = urllib_get,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    max_seconds: float | None = None,
) -> RunSummary:
    """Fetch and normalize all sources concurrently, one worker thread per source.

    max_seconds, if given, bounds the whole run's wall-clock time. A source
    whose deadline has already passed by the time it gets scheduled is
    reported as failed rather than attempted.

    Sources are independent (no shared mutable state), so running them on
    separate threads needs no changes to run_source() or anything below it.
    """
    deadline = now_fn() + max_seconds if max_seconds is not None else None
    source_names = list(FIELD_MAPS)
    run_started_at = now_fn()
    with ThreadPoolExecutor(max_workers=len(source_names)) as executor:
        results = list(
            executor.map(
                lambda source_name: run_source(base_url, source_name, fetch_page, sleep_fn, now_fn, deadline),
                source_names,
            )
        )
    return RunSummary(results=results, wall_clock_seconds=now_fn() - run_started_at)


def main() -> None:
    """CLI entry point: run() against the real mock service and print a summary."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    summary = run()
    for result in summary.results:
        if result.error is not None:
            print(
                f"{result.source}: FAILED - {result.error} "
                f"({result.elapsed_seconds:.2f}s, {result.waits} waits)"
            )
        else:
            print(
                f"{result.source}: {len(result.records)} records, "
                f"{result.skipped} skipped, {result.duplicates} duplicates "
                f"({result.elapsed_seconds:.2f}s, {result.waits} waits)"
            )
    print(
        f"Overall: {summary.status} - {summary.total_records} records, "
        f"{summary.total_skipped} skipped, {summary.total_duplicates} duplicates, "
        f"{summary.wall_clock_seconds:.2f}s wall clock ({summary.total_source_seconds:.2f}s summed across sources)"
    )


if __name__ == "__main__":
    main()
