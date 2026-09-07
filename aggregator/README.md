# README

## Setup Instructions

Start the mock API service first, from `mock-api-service/`, either directly:

```bash
python3 server.py --port 8080
```

or through Docker with `docker compose up --build`.

There are no third-party dependencies anywhere in this project. It uses the standard library only.

## Run Instructions

From the `aggregator/` directory, with the mock service running on `localhost:8080`:

```bash
python3 -m aggregator.orchestrator
```

Sources are fetched concurrently, one thread per source. This prints a per-source summary (records, skipped, duplicates, elapsed time, wait count, or the failure reason) and an overall `success`, `partial_success`, or `failed` status with cross-source totals, including both the real wall-clock time for the run and the sum of each source's own time. Retry attempts, source-level failures, and skipped malformed or duplicate records are also logged through Python's `logging` module at WARNING or ERROR level.

## Test Instructions

```bash
python3 -m unittest discover -s tests -v
```

All 88 tests are unit tests. There is no network access, and no dependency on the mock service running. HTTP is mocked at the `urllib` boundary, and the retry and rate-limit engines take injectable `sleep_fn` and `now_fn`. A few tests use a real, short `time.sleep` specifically to prove sources run concurrently rather than sequentially. The mock-server run described above is the manual integration check.

## Language/Framework Choice

Python, standard library only (`urllib`, `unittest`). No third-party packages, matching the mock service's own dependency-free style.

## Important Assumptions

See `SPEC.md` for the full list. Highlights include a single global retry budget not tuned per source, `NaN`, `Infinity`, negative prices, booleans, and nulls all rejected as malformed rather than coerced, a 10-page pagination cap as a defensive guard, and Source C throttled proactively using its own rate-limit response headers when available.

## Known Limitations

- Sources run concurrently on separate threads, but pagination within a single source is still sequential, since each page's URL can depend on the previous page's response (cursor pagination). A full run against the mock service currently takes about 3 to 4 seconds, down from about 5 to 6 seconds when sequential, since the slowest source (Source B's transient-failure retries) no longer blocks the others.
- The overall run deadline (`max_seconds` on `run()`) is soft. It stops a source from starting or continuing once the deadline has passed, but it can't interrupt a single request that's already blocked waiting on the network. Only that request's own timeout (`DEFAULT_TIMEOUT_SECONDS` in `transport.py`) can end it. There's also no CLI flag for `max_seconds` yet, only a function parameter.
- This has only ever been tested against this one mock service. A few things are tuned to its specific behavior and might not transfer directly to a different real-world API. The `X-RateLimit-Limit` and `X-RateLimit-Window` header names it uses aren't an HTTP standard. `MAX_PAGES = 10` assumes a small known page count. `MAX_ATTEMPTS = 3` happens to just cover Source B's worst known failure sequence, rather than being derived independently.
- `SourceResult.waits` combines retry-backoff waits and Source C's throttle waits into one number, rather than reporting them separately. See SPEC.md's Assumptions.
- The run summary doesn't include the `type_coerced_count` signal described in SPEC.md. Numeric-typed ids being coerced to strings isn't currently counted, only rejected malformed values are.
- Duplicate handling is same-source-id-only, as scoped in SPEC.md's Non-Goals. There is no cross-source semantic dedup.

## Anything Intentionally Not Implemented Because of the Time Limit

- A hard run-timeout that can interrupt a request already in flight. Only the soft, between-request deadline described above is implemented.
- The `type_coerced_count` observability signal for numeric-typed ids.
- An automated integration test against a live mock server. Verified manually instead, per PLAN.md.
