# PLAN

## Implementation Approach

- Field normalization: each source has a declarative field map (target field -> source key + type caster). A shared `normalize()` function looks up each target field in the map, applies the caster, and raises a per-record error on failure (missing key / bad type) so bad records can be skipped without failing the whole batch. Per-source branching (if/else on source name) only decides which map to use, not how normalization itself works.

  中文翻译：字段归一化采用声明式映射表（目标字段 -> 源字段名 + 类型转换函数），每个 source 各有一份。共用一个 `normalize()` 函数按映射表逐字段取值并转换类型，失败时（缺字段/类型错误）抛出针对单条记录的异常，从而只丢弃坏记录而不影响整批数据。按 source 名称做的 if/else 判断只用于选择该用哪张映射表，而不是重新实现归一化逻辑本身。

### Build order (layers)

1. **Normalizer** — field map + type caster per source, with type coercion/synchronization (e.g. int cents -> float dollars, string price -> float) so all sources emit the same field types. Built and unit-tested first, directly against `fixtures.json`, with no HTTP or retry logic involved.

   中文：先做归一化层，包含每个 source 的字段映射和类型转换（做"类型同步"，比如把整数分转成 float 美元、把字符串价格转成 float），确保不同 source 输出的字段类型一致。这一层先用 `fixtures.json` 里的静态数据直接单测，不涉及 HTTP 或重试逻辑。

2. **HTTP retry handler** — a single generic retry wrapper around each request, added after the normalizer is working. Retry count is a uniform constant (e.g. 3 attempts) across all sources and status codes — not tuned per source or per code — to keep the handler generic as source count grows. What differs per response is only whether it's classified as retryable (5xx, 429, timeout/connection error) vs terminal (other 4xx). Each attempt is tracked by a per-request retry counter; the wait between attempts is driven by a resettable backoff timer that prefers the server's `Retry-After` value when present (used both for transient 502/503 failures and for 429 rate-limit responses), falling back to a fixed/exponential delay otherwise. Conceptually 429 is a proactive rate-limit signal while 502/503 are reactive failure signals, but both are handled by the same retry engine.

   中文：归一化层跑通之后，第二步加入通用 HTTP 重试处理器,包在每次请求外层。重试次数在所有 source 和所有 status code 之间保持统一常量（比如 3 次），不按 source 或按 code 单独调整，以保证这层不会随 source 数量增多而膨胀。真正因响应而异的只是"该不该重试"的判定（5xx / 429 / 超时/连接错误 = 可重试，其他 4xx = 终止）。每次请求有独立的 retry 计数器,重试间隔由一个可重置的 backoff 计时器控制，优先读取服务器返回的 `Retry-After`（502/503 的瞬时故障和 429 的限流场景都会用到），没有的话再退回固定/指数退避。429 本质上是"主动限流信号"，502/503 是"被动故障信号"，但都走同一套重试引擎。

3. **Concurrency (multithreading)** — sources are fetched concurrently (one worker per source) once the retry handler is stable, so a slow/retrying source doesn't block the others. Within a source, pagination stays sequential when the source has a rate limit (Source C), using a shared throttle (min interval between requests / token bucket) so concurrency at the source level never violates a single source's own rate limit.

   中文：重试处理器稳定之后，第三步加入并发——每个 source 起一个 worker 并行抓取，这样某个 source 的重试/延迟不会拖慢其他 source。但在单个 source 内部，如果该 source 有限流（Source C），分页请求仍保持串行并通过共享节流器（最小请求间隔/令牌桶）控制节奏,确保跨 source 的并发不会破坏单个 source 自身的限流约束。

4. **Aggregation & run summary** — after all sources return (fully, partially, or failed), merge normalized records, apply duplicate handling, and compute run-level stats (per-source success/failure counts, retry counts, skipped/malformed record counts, timing) into the final output.

   中文：所有 source 返回结果后（完整成功、部分成功或彻底失败），合并归一化后的记录、处理重复数据，并计算 run 级别的统计信息（每个 source 的成功/失败数、重试次数、被跳过的坏记录数、耗时）,组成最终输出。

## Major Technical Decisions

-

## Important Tradeoffs

-

## Testing and Verification Strategy

-
