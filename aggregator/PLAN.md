# PLAN

## Implementation Approach

- **【声明式字段映射】** Field normalization: each source has a declarative field map (target field -> source key + type caster). A shared `normalize()` function looks up each target field in the map, applies the caster, and raises a per-record error on failure (missing key / bad type) so bad records can be skipped without failing the whole batch. Per-source branching (if/else on source name) only decides which map to use, not how normalization itself works.

### Build order (layers)

1. **【归一化层】 Normalizer** `[DONE]` — field map + type caster per source, with type coercion/synchronization (e.g. int cents -> float dollars, string price -> float) so all sources emit the same field types. Built and unit-tested first, directly against `fixtures.json`, with no HTTP or retry logic involved.

2. **【HTTP 重试层】 HTTP retry handler** `[DONE]` — a single generic retry wrapper around each request, added after the normalizer is working. Retry count is a uniform constant (e.g. 3 attempts) across all sources and status codes — not tuned per source or per code — to keep the handler generic as source count grows. What differs per response is only whether it's classified as retryable (5xx, 429, timeout/connection error) vs terminal (other 4xx). Each attempt is tracked by a per-request retry counter; the wait between attempts is driven by a resettable backoff timer that prefers the server's `Retry-After` value when present (used both for transient 502/503 failures and for 429 rate-limit responses), falling back to a fixed/exponential delay otherwise. Conceptually 429 is a proactive rate-limit signal while 502/503 are reactive failure signals, but both are handled by the same retry engine.

3. **【真实 transport 适配器】 HTTP transport adapter** `[DONE]` — a thin `urllib`-based adapter (stdlib only) that performs the actual request and translates results into what `fetch_with_retry` expects: a successful response becomes `HTTPResponse`; an `urllib.error.HTTPError` (4xx/5xx) is caught and translated into an `HTTPResponse` with that status code rather than left to propagate, so the retry engine can see and act on the status code; `urllib.error.URLError`/socket timeouts are translated into `ConnectionError`/`TimeoutError` so the existing retry engine handles them without modification. Tested by mocking `urllib.request.urlopen` (same test-double approach as the retry engine), not against a live server.

4. **【分页循环】 Pagination** `[DONE]` — per-source loop that keeps calling the transport+retry layer and advancing the pagination cursor/page/offset until the source reports it's done (or fails permanently). Each of the three pagination styles (page number, cursor, offset/limit) is a small, separate strategy so adding a new source with a known pagination style doesn't require new looping logic.

5. **【编排层 / Orchestrator】 main** `[DONE]` — ties transport + retry + pagination + `normalize()` together across all three sources. Runs each source's fetch-and-normalize independently, catching `RetryExhaustedError` (source-level failure) and `MalformedRecordError` (record-level failure) separately so one source's or one record's failure never stops the others. This is the piece that turns the two already-working layers into something runnable end-to-end.

6. **【并发层】 Concurrency (multithreading)** `[TODO]` — sources are fetched concurrently (one worker per source) once the orchestrator works sequentially, so a slow/retrying source doesn't block the others. Within a source, pagination stays sequential when the source has a rate limit (Source C), using a shared throttle (min interval between requests / token bucket) so concurrency at the source level never violates a single source's own rate limit.

7. **【聚合/汇总层】 Aggregation & run summary** `[TODO]` — after all sources return (fully, partially, or failed), merge normalized records, apply duplicate handling (same-source id collisions), and compute run-level stats (per-source success/failure counts, retry counts, skipped/malformed record counts, timing) into the final output. This is also where the `type_coerced_count` signal from SPEC.md's Assumptions gets surfaced.

## Major Technical Decisions

- **【引擎与配置分离】** `normalize()` is one generic engine; all per-source knowledge lives in declarative field maps (`sources.py`), not in branching logic. Adding a source means adding a map entry, not touching the engine — this was validated by reasoning through "what if there were 50 sources instead of 3."

- **【统一 retry 常量】** A single global `MAX_ATTEMPTS` constant governs retries for every source and every retryable status code, rather than per-source/per-code tuning, because the system can't know in advance how many retries an arbitrary upstream needs.

- **【统一失败类型】** `HTTPError` (status-code exhaustion) and `TransportError` (network-exception exhaustion, wrapping the original error) share a common `RetryExhaustedError` base, so the orchestrator (once built) can catch one type for "this fetch ultimately failed" instead of enumerating exception types.

- **【拒绝而非猜测】** Ambiguous/malformed values (`None`, `bool`, `NaN`, `Infinity`, nested structures, negative price) are rejected as malformed rather than silently coerced to a plausible-looking default (e.g. `0`), because a silently-wrong value is more dangerous than a visibly-dropped record.

## Important Tradeoffs

- **【统一 retry 常量 vs 按 source 调优】** A uniform retry constant is simpler and scales to more sources without code changes, but may retry a source more than it needs or give up before a slower source would have recovered. Accepted this cost in exchange for genericity.

- **【拒绝畸形值 vs 尽量保留数据】** Rejecting ambiguous values (NaN/bool/negative price/etc.) maximizes correctness but minimizes yield — some technically-recoverable records are dropped rather than guessed at. Chosen because a wrong-but-plausible value silently corrupting downstream aggregates is worse than a smaller, trustworthy result set.

- **【手动集成验证 vs 自动化集成测试】** Real end-to-end verification against the running mock server is done manually (see README) instead of as an automated test, to avoid the complexity of managing a server subprocess in the test suite within the time budget. This trades some regression safety for simplicity.

## Testing and Verification Strategy

- **【测试覆盖范围】** Unit tests (no network, no real sleeps) cover: per-source happy-path normalization; malformed and missing-field records; transient 502/503/429 retry-then-succeed and retry-exhausted paths, including the exact failure-then-success boundary matching Source B's real cursor-3 behavior; mixed error-code sequences (retryable followed by non-retryable, or two different retryable codes back-to-back); timeout/connection-error handling; and a malformed `Retry-After` header falling back to the default backoff. The retry engine's tests are organized by generic engine behavior rather than duplicated per source, since `fetch_with_retry()` doesn't know which source it's called for.

- **【故意不测的部分】** Deliberately not covered by automated tests: a real integration run against the running mock server (verified manually instead — see README); load/concurrency stress testing; the aggregation/run-summary layer (not yet implemented).
