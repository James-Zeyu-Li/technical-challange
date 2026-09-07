# README

## Setup Instructions

Start the mock API service first (from `mock-api-service/`), either directly:

```bash
python3 server.py --port 8080
```

or via Docker: `docker compose up --build`.

No third-party dependencies anywhere in this project (stdlib only).

## Run Instructions

From the `aggregator/` directory, with the mock service running on `localhost:8080`:

```bash
python3 -m aggregator.orchestrator
```

Prints a per-source summary (records/skipped/duplicates, elapsed time, wait count, or the failure reason) and an overall `success` / `partial_success` / `failed` status with cross-source totals. Retry attempts, source-level failures, and skipped malformed/duplicate records are also logged via Python's `logging` module at WARNING/ERROR level.

## Test Instructions

```bash
python3 -m unittest discover -s tests -v
```

All 80 tests are unit tests (no network, no real sleeps, no dependency on the mock service running) — HTTP is mocked at the `urllib` boundary and the retry/rate-limit engines take injectable `sleep_fn`/`now_fn`. The mock-server run above is the manual integration check.

## Language/Framework Choice

Python, stdlib only (`urllib`, `unittest`) — no third-party packages, matching the mock service's own dependency-free style.

## Important Assumptions

See `SPEC.md` for the full list. Highlights: a single global retry budget (not tuned per source), `NaN`/`Infinity`/negative prices/booleans/nulls rejected as malformed rather than coerced, a 10-page pagination cap as a defensive guard, and Source C throttled proactively using its own rate-limit response headers when available.

## Known Limitations

- Sources are fetched sequentially, not concurrently (`PLAN.md`'s concurrency layer isn't built) — a slow/retrying source delays the others. A full run against the mock service currently takes ~5-6s; acceptable for this exercise's scale, deferred deliberately rather than an oversight (see `PLAN.md`'s build order).
- This has only ever been tested against this one mock service. A few things are tuned to its specific behavior and might not transfer directly to a different real-world API: the `X-RateLimit-Limit`/`X-RateLimit-Window` header names it uses aren't an HTTP standard; `MAX_PAGES = 10` assumes a small known page count; `MAX_ATTEMPTS = 3` happens to just cover Source B's worst known failure sequence rather than being derived independently.
- `SourceResult.waits` combines retry-backoff waits and Source C's throttle waits into one number rather than reporting them separately (see `SPEC.md`'s Assumptions).
- The run summary doesn't include the `type_coerced_count` signal described in `SPEC.md` (numeric-typed ids being coerced to strings isn't currently counted, only rejected malformed values are).
- Duplicate handling is same-source-id-only, as scoped in `SPEC.md`'s Non-Goals; no cross-source semantic dedup.

## Anything Intentionally Not Implemented Because of the Time Limit

- Concurrency/multithreading across sources.
- The `type_coerced_count` observability signal for numeric-typed ids.
- Automated integration test against a live mock server (verified manually instead, per `PLAN.md`).
