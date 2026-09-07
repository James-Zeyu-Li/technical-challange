# SPEC

## Goals

- Normalize records from three schema-heterogeneous sources into a single unified product format (id, title, source, price, category).
- Retry transient upstream failures (5xx, 429, timeouts) a bounded number of times so a recoverable failure doesn't cause data loss.
- Respect upstream rate limits so concurrent fetching doesn't repeatedly trigger 429s.
- Isolate bad individual records so a single malformed record doesn't affect other records or the overall run outcome.
- Isolate source-level failures so one source failing completely doesn't prevent the other sources' results from being returned. This is partial success.
- Produce a run-level summary (per-source success and failure counts, retry counts, skipped and malformed record counts, timing) for observability.

## Non-Goals

- Not implementing or modifying the mock upstream APIs themselves.
- Not tuning retry counts or timeouts per source or per status code. A single global policy is used instead. See Assumptions.
- Not building production infrastructure (database, deployment config, CI, containerizing this service, monitoring stack) unless it materially helps demonstrate a decision.
- Not implementing cross-source semantic deduplication, such as matching the "same" product listed under different ids across sources. Only exact id and source collisions are considered.

## Important Requirements

- Fetch all three sources, each with its own pagination style (page number, cursor, or offset and limit), until each is fully paginated or fails permanently.
- Normalize every successfully fetched raw record through the shared `normalize()` function and per-source field map. See PLAN.md.
- Retry only classified-as-retryable responses (5xx, 429, timeout or connection error). Other 4xx responses fail immediately without retry.
- Honor a `Retry-After` header when present. Fall back to a fixed default backoff when absent.
- Throttle requests to any source with a known rate limit (Source C) so the client itself does not exceed that limit under concurrency.

## Failure Behavior

- A single malformed record (missing field or failed type cast) is skipped and counted, not treated as a fatal error for its source.
- A source that exhausts its retry budget on a given page is marked as partially or fully failed for this run, but does not stop the other sources from completing.
- A run is successful if at least one source returns data. It is partially successful if one or more sources failed while others succeeded. It is failed only if all sources failed.
- Duplicate id and source pairs across pages, which should not normally occur, keep the first-seen record. The rest are counted as duplicates in the summary.

## Assumptions or Ambiguities

### Retry and Transport

- **Global retry constant.** Retry count is one global constant, not tuned per source or status code, since the system can't know in advance how many retries an arbitrary upstream needs.
- **Magic numbers, flagged honestly.** `DEFAULT_BACKOFF_SECONDS = 0.5`, `DEFAULT_TIMEOUT_SECONDS = 5.0`, and `MARGIN_MULTIPLIER = 1.2` are reasonable defaults, not measured values. `MAX_ATTEMPTS` is different, since it's justified against Source B's real cursor-3 failure count.
- **Logging isn't test-injectable.** Retry attempts, source failures, and skipped records are logged through stdlib `logging`. Unlike `sleep_fn`/`now_fn`, logging is a diagnostic side channel, not business logic, so it isn't made assertable in tests.

### Pagination and Rate Limiting

- **Pagination safety cap.** `fetch_all_pages()` has a hard `MAX_PAGES = 10` cap and raises `PaginationLimitExceededError` if exceeded. This guards against a source whose pagination metadata never signals "done." No known source needs more than 3 pages.
- **Malformed pagination envelope is a source-level failure.** Missing or wrong-typed pagination fields raise `MalformedPaginationEnvelopeError`, handled like `RetryExhaustedError`, rather than guessing whether more pages exist. This is distinct from a malformed record, which is skipped individually.
- **Source C is throttled proactively.** Pagination spaces out requests instead of relying only on reactive 429-retry, since 3 unthrottled requests would likely trigger 429 under its real 2-per-second limit. Sources A and B are never throttled.
- **Throttle interval is derived, not hardcoded.** `RateLimiter` prefers the source's own `X-RateLimit-Limit`/`X-RateLimit-Window` headers, deriving `(window / limit) × 1.2`. A static default (0.6s for Source C) applies until those headers are seen, or as a fallback. Each source's `RateLimiter` is independent, with no shared state.
- **Waits are a combined count.** `SourceResult.waits` counts retry-backoff waits and throttle waits together, not split apart, since splitting would require changing `fetch_all_pages()`'s return contract.

### Data Quality and Normalization

- **Type looseness versus structural corruption.** A number-typed id or sku is accepted and coerced to a string, since that's just loose typing. A null value or nested structure in a scalar field is treated as malformed, since that indicates real data corruption.
  - Coercion should still surface as a `type_coerced_count` alongside malformed/skipped counts, so schema drift stays visible. Deferred, not yet implemented.
- **NaN and Infinity are rejected, not coerced to 0.** Python's `float()` accepts them, but letting either into `price` would silently corrupt downstream math. Rejecting is safer than a wrong-but-plausible value.
- **Price must be `>= 0`.** A business rule, not a type-safety concern, so it lives in a dedicated `to_price()` caster rather than the generic `to_float()`.
- **All four schema fields are required.** `id`, `title`, `price`, `category`, matching `task.md`'s "at minimum" definition. No optional-field concept yet.

### Concurrency

- **Sources are fetched concurrently, one thread per source.** Each source has no shared mutable state (its own `RateLimiter`, its own dedup set), so this needed no changes to the fetch or pagination layers.
- **The overall run deadline is soft, not a hard cancellation.** An optional `max_seconds` on `run()` is checked before each request within a source's pagination loop. It stops a source from starting or continuing once passed, but it can't interrupt a single request already blocked on the network. Only that request's own timeout can end it.
- **`RunSummary` reports both a real wall-clock time and a summed per-source time.** Since sources overlap in time, summing each source's own elapsed time would overcount the run's real duration, so the two are tracked separately.

### Scope

- **`/admin/reset` is test tooling only.** It is not something the aggregator calls during normal operation.
- **Cross-source id collisions are assumed to be coincidental.** Dedup logic only applies within a single source, not across sources.

## Testable Acceptance Criteria

- Given a raw record with a non-numeric price field, such as Source B's `b-205`, `normalize()` raises `MalformedRecordError` and the record is excluded from the final output.
- Given Source B's cursor-2 and cursor-3 transient failure sequence, the pipeline still returns all Source B records after retrying within the global retry budget.
- Given Source C's rate limit, concurrent pagination does not exceed 2 requests per second and completes without an unhandled 429.
- Given one source fails completely after exhausting its retry budget, the run still returns normalized records from the other two sources and reports the failed source in the summary.
