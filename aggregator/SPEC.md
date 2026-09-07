# SPEC

## Goals and Non-Goals

- Normalize records from three schema-heterogeneous sources into a single unified product format (id/title/source/price/category).
- Retry transient upstream failures (5xx, 429, timeouts) a bounded number of times so a recoverable failure doesn't cause data loss.
- Respect upstream rate limits so concurrent fetching doesn't repeatedly trigger 429s.
- Isolate bad individual records so a single malformed record doesn't affect other records or the overall run outcome.
- Isolate source-level failures so one source failing completely doesn't prevent the other sources' results from being returned (partial success).
- Produce a run-level summary (per-source success/failure counts, retry counts, skipped/malformed record counts, timing) for observability.

## Non-Goals

- Not implementing or modifying the mock upstream APIs themselves.
- Not tuning retry counts or timeouts per source/per status code — a single global policy is used instead (see Assumptions).
- Not building production infrastructure (database, deployment config, CI, containerizing this service, monitoring stack) unless it materially helps demonstrate a decision.
- Not implementing cross-source semantic deduplication (e.g. matching the "same" product listed under different ids across sources) — only exact id+source collisions are considered.

## Important Requirements

- Fetch all three sources, each with its own pagination style (page number / cursor / offset-limit), until each is fully paginated or fails permanently.
- Normalize every successfully fetched raw record via the shared `normalize()` function and per-source field map (see PLAN.md).
- Retry only classified-as-retryable responses (5xx, 429, timeout/connection error); other 4xx responses fail immediately without retry.
- Honor a `Retry-After` header when present; fall back to a fixed default backoff when absent.
- Throttle requests to any source with a known rate limit (Source C) so the client itself does not exceed that limit under concurrency.

## Failure Behavior

- A single malformed record (missing field or failed type cast) is skipped and counted, not treated as a fatal error for its source.
- A source that exhausts its retry budget on a given page is marked as partially or fully failed for this run, but does not stop the other sources from completing.
- A run is "successful" if at least one source returns data; "partially successful" if one or more sources failed while others succeeded; "failed" only if all sources failed.
- Duplicate id+source pairs across pages (should not normally occur) keep the first-seen record and count the rest as duplicates in the summary.

## Assumptions or Ambiguities

- Retry count is a single global constant (not tuned per source/status code) because the system cannot know in advance how many retries an arbitrary upstream needs; the constant represents an accepted cost/latency tradeoff, not a fit to this mock's specific failure counts.
- `POST /admin/reset` on the mock service is a test-setup utility, not something the aggregator calls during normal operation.
- Cross-source id collisions are assumed to be coincidental, not the same product — dedup logic only applies within a single source.
- Sources are not assumed to always send the documented type for a field. A number-typed id/sku is accepted (coerced to string) since that's just a loose-typing difference; a null value or a nested structure (dict/list) in a scalar field is treated as malformed, since that indicates upstream data corruption rather than a harmless type difference.
  - A record accepted this way is not rejected, but the run summary should still surface that type coercion happened (e.g. a `type_coerced_count` alongside the malformed/skipped counts), so schema drift stays visible even when it isn't fatal. This tracking is deferred to the aggregation/run-summary layer, which is not yet implemented.
- `NaN` and `Infinity` are rejected as malformed, not coerced to `0`. Python's `float()` happily accepts `"nan"`/`"inf"`, but silently letting either into `price` would corrupt downstream sum/sort/compare without any visible error — a rejected record is safer than one with a wrong-but-plausible-looking value.
- Price must be `>= 0`. This is a business rule, not a type-safety concern, so it lives in a dedicated `to_price()` caster (used only for the `price` field) rather than in the generic `to_float()` used elsewhere — keeping numeric type-safety and price-specific validation as separate concerns.
- `fetch_all_pages()` has a hard `MAX_PAGES = 10` cap, raising `PaginationLimitExceededError` if exceeded. This is a defensive guard against a source whose pagination metadata never signals "done" (e.g. a buggy or cyclic `next_cursor`), not a value expected to be hit by any of the three known sources (max 3 pages each). 10 was chosen over a much larger number (e.g. 1000) because this is a small exercise with known, bounded sources — it only needs to be comfortably larger than the known max, not defend against arbitrary future scale.
- If a page's pagination-control fields (`total_pages`/`next_cursor`/`next_offset`/the records key itself) are missing or the wrong type, `fetch_all_pages()` raises `MalformedPaginationEnvelopeError` rather than guessing whether more pages exist or silently stopping. This is handled the same way as `RetryExhaustedError` by the orchestrator (source-level failure, isolated from other sources) — distinct from a malformed *record*, which is skipped individually instead.
- Pagination proactively spaces requests to Source C instead of relying only on reactive 429-retry, since 3 sequential page requests without throttling would likely trigger 429 under Source C's real 2-requests/second limit. Sources without a known rate limit (A, B) are never throttled.
- The throttle interval is not a hardcoded guess. `RateLimiter` (`aggregator/rate_limiter.py`) prefers the source's own `X-RateLimit-Limit`/`X-RateLimit-Window` response headers, deriving `(window / limit) × 1.2` (the 1.2 factor is margin for timing jitter above the mathematical minimum). A static per-source default (0.6s for Source C) is used only until the first response with those headers arrives, and as a fallback if a source never sends them. Each source's `RateLimiter` instance is independent, created fresh per `fetch_all_pages()` call — no shared/global throttling state.
- All four fields in the unified schema (`id`, `title`, `price`, `category`) are required, matching `task.md`'s "at minimum" definition of the normalized product. There is currently no concept of an optional field; adding one is deferred until a real need for optional/extended fields arises (see task.md's "you may extend this model where useful").
- `SourceResult.waits` counts every sleep event for that source — both retry-backoff waits (`http_retry.py`) and Source C's proactive throttle waits (`rate_limiter.py`) — as one combined number, not split apart. Splitting them would require changing `fetch_all_pages()`'s return contract (currently a bare `list[dict]`) and touching its existing tests; the combined count is enough to answer "did this source spend time waiting" without that cost.
- Retry attempts (`http_retry.py`), source-level failures, and skipped malformed/duplicate records (`orchestrator.py`) are logged via the stdlib `logging` module at WARNING/ERROR level. Unlike `sleep_fn`/`now_fn`, logging is not made injectable/assertable in tests — it's a side channel for human diagnosis, not business logic, so it isn't worth the added parameter surface to unit-test its exact output.
- `DEFAULT_BACKOFF_SECONDS = 0.5` (`http_retry.py`), `DEFAULT_TIMEOUT_SECONDS = 5.0` (`transport.py`), and `MARGIN_MULTIPLIER = 1.2` (`rate_limiter.py`) are reasonable engineering defaults, not values derived from measurement or a stronger requirement — unlike `MAX_ATTEMPTS`, which is justified against Source B's real cursor-3 failure count. Flagged here explicitly rather than implying a rigor that isn't there.

## Testable Acceptance Criteria

- Given a raw record with a non-numeric price field (e.g. Source B's `b-205`), `normalize()` raises `MalformedRecordError` and the record is excluded from the final output.
- Given Source B's cursor-2/cursor-3 transient failure sequence, the pipeline still returns all Source B records after retrying within the global retry budget.
- Given Source C's rate limit, concurrent pagination does not exceed 2 requests/second and completes without an unhandled 429.
- Given one source fails completely (retry budget exhausted), the run still returns normalized records from the other two sources and reports the failed source in the summary.
