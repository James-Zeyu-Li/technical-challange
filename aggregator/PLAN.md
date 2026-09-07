# PLAN

## Implementation Approach

- **Field normalization.** Each source has a declarative field map from target field to source key plus a type caster. A shared `normalize()` looks up each field, applies the caster, and raises a per-record error on failure, so bad records can be skipped without failing the whole batch. Per-source branching only picks which map to use.

### Build order (layers)

1. **Normalizer** `[DONE]`. Field map plus type caster per source, with type coercion (int cents to float dollars, string price to float). Unit-tested first against `fixtures.json`, with no HTTP involved.
2. **HTTP retry handler** `[DONE]`. One generic retry wrapper per request. Retry count is a uniform constant, not tuned per source or code. Retryable statuses (5xx, 429, timeout, connection error) back off using `Retry-After` when present, otherwise a fixed delay. 429 and 5xx are handled by the same engine.
3. **HTTP transport adapter** `[DONE]`. A thin stdlib `urllib` adapter. Translates `HTTPError` (4xx/5xx) into a normal `HTTPResponse` so the retry engine can see the status code, and translates `URLError`/socket timeouts into `ConnectionError`/`TimeoutError`. Tested by mocking `urlopen`, not against a live server.
4. **Pagination** `[DONE]`. A per-source loop over the transport and retry layer, advancing cursor/page/offset until done or permanently failed. Each pagination style (page number, cursor, offset+limit) is a small, separate strategy.
5. **Orchestrator / main** `[DONE]`. Ties transport, retry, pagination, and `normalize()` together across all three sources, catching source-level failures and record-level failures separately so one failure never stops the rest.
6. **Concurrency** `[DONE]`. Sources are fetched in parallel with one thread per source through `ThreadPoolExecutor`. Each source has no shared mutable state, so this needed no changes to `run_source()` or anything below it. Pagination stays sequential within a rate-limited source, using the existing per-source throttle. An optional `max_seconds` on `run()` sets a soft overall deadline, checked between requests, not able to interrupt one already in flight.
7. **Aggregation and run summary** `[PARTIAL]`. Duplicate handling and run-level stats (records, skipped, duplicates, elapsed time, wait count, cross-source totals, plus a real wall-clock time for the concurrent run) are done. Still missing is the `type_coerced_count` signal.

## Major Technical Decisions

- **Engine versus config.** `normalize()` is one generic engine. Per-source knowledge lives only in declarative field maps, validated by reasoning through "what if there were 50 sources instead of 3."
- **One global retry constant.** A single `MAX_ATTEMPTS` governs every source and status code, since the system can't know in advance how many retries an arbitrary upstream needs.
- **Unified failure type.** `HTTPError` and `TransportError` share a `RetryExhaustedError` base, so callers catch one type for "this fetch ultimately failed."
- **Reject rather than guess.** Ambiguous values (`None`, `bool`, `NaN`, `Infinity`, nested structures, negative price) are rejected, since a silently-wrong value is worse than a dropped record.

## Important Tradeoffs

- **Uniform retry constant versus per-source tuning.** Simpler and scales without code changes, but may over-retry or give up too early for a given source. Accepted for genericity.
- **Rejecting malformed values versus maximizing yield.** Drops some technically-recoverable records rather than guessing. A wrong-but-plausible value corrupting downstream aggregates is worse than a smaller, trustworthy result.
- **Manual versus automated integration verification.** Verified manually against the running mock server instead of automating it, to avoid managing a server subprocess within the time budget.
- **Dynamic throttle interval versus a static constant.** More robust, but the first request still uses a static guess, and the header names are this mock's own convention, not a standard. Accepted since the mock already provides trustworthy data.

## Testing and Verification Strategy

Unit tests only. No network, no real sleeps, `sleep_fn`/`now_fn` are injected.

### Covered

- Per-source normalization, plus malformed and missing-field records.
- Transient 502/503/429 retry paths, including the exact boundary matching Source B's real cursor-3 behavior.
- Mixed error-code sequences, timeout/connection errors, and a malformed `Retry-After` header.
- Pagination across all three styles, envelope corruption, the safety cap, and Source C's throttling.
- Source-level failure isolation, duplicate handling, and run-level observability fields.
- Concurrent fetching (real wall-clock overlap, not just the sequential-equivalent result), stable result ordering, and the overall run deadline.

### Deliberately not covered

- A real integration run against the mock server. Verified manually instead, see README.
- Load and concurrency stress testing.
- The `type_coerced_count` signal.
