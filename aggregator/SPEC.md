# SPEC

## Goals and Non-Goals

- Normalize records from three schema-heterogeneous sources into a single unified product format (id/title/source/price/category).

  中文：把三个 schema 各不相同的 source 归一化成统一的产品格式（id/title/source/price/category）。

- Retry transient upstream failures (5xx, 429, timeouts) a bounded number of times so a recoverable failure doesn't cause data loss.

  中文：对瞬时性上游故障（5xx、429、超时）做有限次数的重试，避免可恢复的故障导致数据丢失。

- Respect upstream rate limits so concurrent fetching doesn't repeatedly trigger 429s.

  中文：遵守上游的限流约束，确保并发抓取不会持续触发 429。

- Isolate bad individual records so a single malformed record doesn't affect other records or the overall run outcome.

  中文：隔离坏的单条记录，确保一条畸形数据不会影响其他记录或整体运行结果。

- Isolate source-level failures so one source failing completely doesn't prevent the other sources' results from being returned (partial success).

  中文：隔离 source 级别的失败，确保某一个 source 彻底失败时，其他 source 的结果仍能正常返回（partial success）。

- Produce a run-level summary (per-source success/failure counts, retry counts, skipped/malformed record counts, timing) for observability.

  中文：产出 run-level 的统计摘要（每个 source 的成功/失败数、重试次数、被跳过的坏记录数、耗时），提供可观测性。

## Non-Goals

- Not implementing or modifying the mock upstream APIs themselves.

  中文：不实现或修改 mock 上游 API 本身。

- Not tuning retry counts or timeouts per source/per status code — a single global policy is used instead (see Assumptions).

  中文：不按 source 或按 status code 单独调优重试次数/超时时间，而是使用统一的全局策略（见 Assumptions）。

- Not building production infrastructure (database, deployment config, CI, containerizing this service, monitoring stack) unless it materially helps demonstrate a decision.

  中文：不搭建生产级基础设施（数据库、部署配置、CI、把本服务容器化、监控系统），除非它能实质性地体现某个工程决策。

- Not implementing cross-source semantic deduplication (e.g. matching the "same" product listed under different ids across sources) — only exact id+source collisions are considered.

  中文：不做跨 source 的语义级去重（比如识别不同 source 下"同一件商品"），只处理 id+source 完全重复的情况。

## Important Requirements

- Fetch all three sources, each with its own pagination style (page number / cursor / offset-limit), until each is fully paginated or fails permanently.

  中文：抓取全部三个 source，各自使用自己的分页方式（page number / cursor / offset-limit），直到翻完全部页或永久失败为止。

- Normalize every successfully fetched raw record via the shared `normalize()` function and per-source field map (see PLAN.md).

  中文：每条成功抓到的原始记录都要经过共用的 `normalize()` 函数和各 source 的字段映射表处理（详见 PLAN.md）。

- Retry only classified-as-retryable responses (5xx, 429, timeout/connection error); other 4xx responses fail immediately without retry.

  中文：只对被判定为"可重试"的响应（5xx、429、超时/连接错误）做重试；其他 4xx 直接判定失败,不重试。

- Honor a `Retry-After` header when present; fall back to a fixed default backoff when absent.

  中文：响应中出现 `Retry-After` 时必须遵守；没有该 header 时使用固定的默认退避时间。

- Throttle requests to any source with a known rate limit (Source C) so the client itself does not exceed that limit under concurrency.

  中文：对已知有限流的 source（Source C）做请求节流，确保并发场景下客户端自己不会超过该限流。

## Failure Behavior

- A single malformed record (missing field or failed type cast) is skipped and counted, not treated as a fatal error for its source.

  中文：单条畸形记录（缺字段或类型转换失败）会被跳过并计数，不会被当作该 source 的致命错误。

- A source that exhausts its retry budget on a given page is marked as partially or fully failed for this run, but does not stop the other sources from completing.

  中文：某个 source 在某一页耗尽重试次数后，本次运行对该 source 判定为部分或完全失败，但不会阻止其他 source 完成。

- A run is "successful" if at least one source returns data; "partially successful" if one or more sources failed while others succeeded; "failed" only if all sources failed.

  中文：只要至少一个 source 返回了数据就判定为"成功"；部分 source 失败、部分成功则判定为"部分成功"；只有全部 source 都失败才判定为"失败"。

- Duplicate id+source pairs across pages (should not normally occur) keep the first-seen record and count the rest as duplicates in the summary.

  中文：同一 source 内出现重复的 id（正常情况下不该发生）时保留第一次出现的记录，其余计入摘要里的重复计数。

## Assumptions or Ambiguities

- Retry count is a single global constant (not tuned per source/status code) because the system cannot know in advance how many retries an arbitrary upstream needs; the constant represents an accepted cost/latency tradeoff, not a fit to this mock's specific failure counts.

  中文：重试次数使用统一的全局常量（不按 source/status code 单独调优），因为系统无法提前知道任意上游需要多少次重试；这个常量代表可接受的成本/延迟权衡，而不是对这个 mock 具体失败次数的拟合。

- `POST /admin/reset` on the mock service is a test-setup utility, not something the aggregator calls during normal operation.

  中文：mock 服务的 `POST /admin/reset` 是测试准备用的工具，不是 aggregator 在正常运行中会调用的接口。

- Cross-source id collisions are assumed to be coincidental, not the same product — dedup logic only applies within a single source.

  中文：假设跨 source 的 id 相同只是巧合，不代表是同一件商品——去重逻辑只在单个 source 内部生效。

## Testable Acceptance Criteria

- Given a raw record with a non-numeric price field (e.g. Source B's `b-205`), `normalize()` raises `MalformedRecordError` and the record is excluded from the final output.

  中文：给定一条价格字段非数字的原始记录（如 Source B 的 `b-205`），`normalize()` 抛出 `MalformedRecordError`，该记录不出现在最终输出中。

- Given Source B's cursor-2/cursor-3 transient failure sequence, the pipeline still returns all Source B records after retrying within the global retry budget.

  中文：给定 Source B 的 cursor-2/cursor-3 瞬时失败序列，pipeline 在全局重试次数内重试后仍能返回 Source B 的全部记录。

- Given Source C's rate limit, concurrent pagination does not exceed 2 requests/second and completes without an unhandled 429.

  中文：给定 Source C 的限流，并发翻页时每秒请求数不超过 2 次，且不会出现未被处理的 429。

- Given one source fails completely (retry budget exhausted), the run still returns normalized records from the other two sources and reports the failed source in the summary.

  中文：给定某个 source 完全失败（重试次数耗尽），本次运行仍能返回另外两个 source 的归一化数据，并在摘要中报告该失败的 source。
