# PLAN

## Implementation Approach

- **【声明式字段映射】** Field normalization: each source has a declarative field map (target field -> source key + type caster). A shared `normalize()` function looks up each target field in the map, applies the caster, and raises a per-record error on failure (missing key / bad type) so bad records can be skipped without failing the whole batch. Per-source branching (if/else on source name) only decides which map to use, not how normalization itself works.

### Build order (layers)

1. **【归一化层】 Normalizer** — field map + type caster per source, with type coercion/synchronization (e.g. int cents -> float dollars, string price -> float) so all sources emit the same field types. Built and unit-tested first, directly against `fixtures.json`, with no HTTP or retry logic involved.

2. **【HTTP 重试层】 HTTP retry handler** — a single generic retry wrapper around each request, added after the normalizer is working. Retry count is a uniform constant (e.g. 3 attempts) across all sources and status codes — not tuned per source or per code — to keep the handler generic as source count grows. What differs per response is only whether it's classified as retryable (5xx, 429, timeout/connection error) vs terminal (other 4xx). Each attempt is tracked by a per-request retry counter; the wait between attempts is driven by a resettable backoff timer that prefers the server's `Retry-After` value when present (used both for transient 502/503 failures and for 429 rate-limit responses), falling back to a fixed/exponential delay otherwise. Conceptually 429 is a proactive rate-limit signal while 502/503 are reactive failure signals, but both are handled by the same retry engine.

3. **【并发层】 Concurrency (multithreading)** — sources are fetched concurrently (one worker per source) once the retry handler is stable, so a slow/retrying source doesn't block the others. Within a source, pagination stays sequential when the source has a rate limit (Source C), using a shared throttle (min interval between requests / token bucket) so concurrency at the source level never violates a single source's own rate limit.

4. **【聚合/汇总层】 Aggregation & run summary** — after all sources return (fully, partially, or failed), merge normalized records, apply duplicate handling, and compute run-level stats (per-source success/failure counts, retry counts, skipped/malformed record counts, timing) into the final output.

## Major Technical Decisions

-

## Important Tradeoffs

-

## Testing and Verification Strategy

- **【测试覆盖范围】** Unit tests (no network, no real sleeps) cover: per-source happy-path normalization; malformed and missing-field records; transient 502/503/429 retry-then-succeed and retry-exhausted paths, including the exact failure-then-success boundary matching Source B's real cursor-3 behavior; mixed error-code sequences (retryable followed by non-retryable, or two different retryable codes back-to-back); timeout/connection-error handling; and a malformed `Retry-After` header falling back to the default backoff. The retry engine's tests are organized by generic engine behavior rather than duplicated per source, since `fetch_with_retry()` doesn't know which source it's called for.

- **【故意不测的部分】** Deliberately not covered by automated tests: a real integration run against the running mock server (verified manually instead — see README); load/concurrency stress testing; the aggregation/run-summary layer (not yet implemented).
