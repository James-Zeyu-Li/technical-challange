# SPEC

## Goals and Non-Goals

- **【归一化】** Normalize records from three schema-heterogeneous sources into a single unified product format (id/title/source/price/category).
- **【瞬时重试】** Retry transient upstream failures (5xx, 429, timeouts) a bounded number of times so a recoverable failure doesn't cause data loss.
- **【限流】** Respect upstream rate limits so concurrent fetching doesn't repeatedly trigger 429s.
- **【坏记录隔离】** Isolate bad individual records so a single malformed record doesn't affect other records or the overall run outcome.
- **【source 级隔离】** Isolate source-level failures so one source failing completely doesn't prevent the other sources' results from being returned (partial success).
- **【run summary】** Produce a run-level summary (per-source success/failure counts, retry counts, skipped/malformed record counts, timing) for observability.

## Non-Goals

- **【不改 mock】** Not implementing or modifying the mock upstream APIs themselves.
- **【不按 source 调优】** Not tuning retry counts or timeouts per source/per status code — a single global policy is used instead (see Assumptions).
- **【不搭生产基建】** Not building production infrastructure (database, deployment config, CI, containerizing this service, monitoring stack) unless it materially helps demonstrate a decision.
- **【不做语义去重】** Not implementing cross-source semantic deduplication (e.g. matching the "same" product listed under different ids across sources) — only exact id+source collisions are considered.

## Important Requirements

- **【全量抓取+分页】** Fetch all three sources, each with its own pagination style (page number / cursor / offset-limit), until each is fully paginated or fails permanently.
- **【统一走 normalize】** Normalize every successfully fetched raw record via the shared `normalize()` function and per-source field map (see PLAN.md).
- **【只重试可重试项】** Retry only classified-as-retryable responses (5xx, 429, timeout/connection error); other 4xx responses fail immediately without retry.
- **【遵守 Retry-After】** Honor a `Retry-After` header when present; fall back to a fixed default backoff when absent.
- **【Source C 节流】** Throttle requests to any source with a known rate limit (Source C) so the client itself does not exceed that limit under concurrency.

## Failure Behavior

- **【坏记录跳过计数】** A single malformed record (missing field or failed type cast) is skipped and counted, not treated as a fatal error for its source.
- **【source 失败判定】** A source that exhausts its retry budget on a given page is marked as partially or fully failed for this run, but does not stop the other sources from completing.
- **【run 成功判定标准】** A run is "successful" if at least one source returns data; "partially successful" if one or more sources failed while others succeeded; "failed" only if all sources failed.
- **【重复 id 处理】** Duplicate id+source pairs across pages (should not normally occur) keep the first-seen record and count the rest as duplicates in the summary.

## Assumptions or Ambiguities

- **【统一 retry 常量】** Retry count is a single global constant (not tuned per source/status code) because the system cannot know in advance how many retries an arbitrary upstream needs; the constant represents an accepted cost/latency tradeoff, not a fit to this mock's specific failure counts.
- **【reset 仅测试用】** `POST /admin/reset` on the mock service is a test-setup utility, not something the aggregator calls during normal operation.
- **【跨 source id 冲突视为巧合】** Cross-source id collisions are assumed to be coincidental, not the same product — dedup logic only applies within a single source.
- **【类型宽容 vs 结构性拒绝】** Sources are not assumed to always send the documented type for a field. A number-typed id/sku is accepted (coerced to string) since that's just a loose-typing difference; a null value or a nested structure (dict/list) in a scalar field is treated as malformed, since that indicates upstream data corruption rather than a harmless type difference.
  - A record accepted this way is not rejected, but the run summary should still surface that type coercion happened (e.g. a `type_coerced_count` alongside the malformed/skipped counts), so schema drift stays visible even when it isn't fatal. This tracking is deferred to the aggregation/run-summary layer, which is not yet implemented.
- **【NaN/Infinity 拒绝】** `NaN` and `Infinity` are rejected as malformed, not coerced to `0`. Python's `float()` happily accepts `"nan"`/`"inf"`, but silently letting either into `price` would corrupt downstream sum/sort/compare without any visible error — a rejected record is safer than one with a wrong-but-plausible-looking value.
- **【价格非负】** Price must be `>= 0`. This is a business rule, not a type-safety concern, so it lives in a dedicated `to_price()` caster (used only for the `price` field) rather than in the generic `to_float()` used elsewhere — keeping numeric type-safety and price-specific validation as separate concerns.
- **【字段目前全部必需】** All four fields in the unified schema (`id`, `title`, `price`, `category`) are required, matching `task.md`'s "at minimum" definition of the normalized product. There is currently no concept of an optional field; adding one is deferred until a real need for optional/extended fields arises (see task.md's "you may extend this model where useful").

## Testable Acceptance Criteria

- **【b-205 坏价格】** Given a raw record with a non-numeric price field (e.g. Source B's `b-205`), `normalize()` raises `MalformedRecordError` and the record is excluded from the final output.
- **【cursor-2/3 瞬时失败恢复】** Given Source B's cursor-2/cursor-3 transient failure sequence, the pipeline still returns all Source B records after retrying within the global retry budget.
- **【Source C 限流验证】** Given Source C's rate limit, concurrent pagination does not exceed 2 requests/second and completes without an unhandled 429.
- **【单 source 完全失败】** Given one source fails completely (retry budget exhausted), the run still returns normalized records from the other two sources and reports the failed source in the summary.
