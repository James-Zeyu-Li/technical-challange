# AI Usage

This document tracks how AI assistance (Claude Code) was used while building the
reliable data pipeline aggregator, per the requirements in `task.md`.

## Tools Used

- **Claude Code (Claude Sonnet 5)**. Used interactively for design discussion, test-driven implementation, and code review throughout the project. See below for how.

## What Was Delegated to AI

- Design discussions before any code. Examples include why retry policy is one global constant instead of tuned per source or status code, whether a malformed pagination envelope should be a source-level failure or silently skipped (chose failure, because we can no longer safely tell if more pages exist), and whether Source C's rate limiting needed a pluggable strategy or just a per-source interval (chose the interval, since there is no evidence of other rate-limit shapes among the three known sources).
- Tests written to encode each decision before the implementation existed. Examples include a synthetic "Source D" fixture for type-robustness cases, kept independent of any real source's schema, and a boundary test matching Source B's real `cursor-3` failure pattern.
- Implementation of all modules (`normalize.py`, `sources.py`, `http_retry.py`, `transport.py`, `pagination.py`, `rate_limiter.py`, `orchestrator.py`) to pass those tests.
- Review passes over already-working code. These review passes surfaced the real issues listed under Findings From Final AI Review, instead of accepting the first passing version.
- Discussed asyncio versus threading for concurrency once there was time to add it, and chose threading, since the whole stack (`urllib`, `time.sleep`) is synchronous and async would have required rewriting every layer and every test, while threading only needed a change inside `run()` itself.

## Feedback From Specification Review

- Rejected per-source or per-status-code retry tuning as overfitting to this mock's known failure sequence. Settled on one global `MAX_ATTEMPTS` as an explicit cost and latency tradeoff.
- Requested that `PLAN.md`'s implementation content be reflected in `SPEC.md` as behavioral goals, keeping `PLAN.md` focused on how. This resulted in SPEC's Goals, Non-Goals, Failure Behavior, Assumptions, and Acceptance Criteria sections.

## Verification Process

- Ran the full test suite (88 tests) after every change. HTTP is mocked at the `urllib` boundary and retry/throttle timing is injected through `sleep_fn` and `now_fn`, so most tests run in milliseconds. A few concurrency tests use a real, short `time.sleep` to prove sources actually overlap in wall-clock time.
- Ran the mock service's own `test_server.py` to confirm its documented contract actually holds, before trusting results measured against it.
- Ran the orchestrator end to end against a real running mock server multiple times, both a direct process and Docker, confirming record, skipped, and duplicate counts matched the known fixture data.
- For bug fixes, wrote a failing test that reproduced the real behavior first. For example, confirmed a negative `Retry-After` really crashes `time.sleep` before fixing it, rather than trusting a fix worked without seeing it fail.

## AI Suggestions Challenged, Rejected, or Independently Validated

- **Challenged.** Claude's first framing suggested picking between Flask and Gin as a web framework. Pushed back, since the task doesn't require an HTTP endpoint at all. A plain CLI is sufficient and avoids unjustified complexity. Final direction was Python stdlib only, no framework.
- **Challenged.** Claude's initial type-casting only rejected `None` and nested structures. Rejected this as too permissive and required it to also reject `bool` (which silently becomes `"True"` or `1.0`), `NaN`, `Infinity`, and negative prices, on the grounds that the three known sources shouldn't be assumed to always send well-typed data.
- **Challenged.** Claude initially tested that type-rejection logic against Source B's real fixture data. Rejected this as conflating a regression test for known bad data with a generic engine test, and requested a synthetic "Source D" fixture instead, so the tests aren't tied to any one real source.
- **Independently validated.** After asking Claude to compare per-source test files against one consolidated file, chose to merge them back. Splitting by source contradicted the project's own claim that normalize is one generic engine, so file organization should match that claim rather than undercut it.
- **Challenged.** Claude's rate limiter for Source C used one hardcoded interval. Pushed for a more general design. This resulted in `RateLimiter` deriving the interval from the source's own rate-limit response headers when present, instead of only a static guess.

## Findings From Final AI Review

Review passes surfaced several real issues. Each was confirmed with a failing test before fixing.

- `to_str` and `to_float` silently accepted `bool` (an `int` subclass in Python), coercing it to `"True"` or `1.0` instead of rejecting it.
- `NaN` and `Infinity` passed Python's `float()` check and would have silently corrupted downstream price math.
- A negative `Retry-After` header would crash a real `sleep_fn`.
- Retry exhaustion raised `HTTPError` for a bad status but raised the raw `TimeoutError` or `ConnectionError` for network failures. Unified both under one `RetryExhaustedError` base.
- `fetch_all_pages()` didn't forward its own `sleep_fn` into each page's retry call, so a backoff silently fell back to real `time.sleep`.
- Source C's throttle interval was a hardcoded guess rather than derived from the mock's own `X-RateLimit-Limit` and `X-RateLimit-Window` headers.
- After adding concurrency, `RunSummary`'s reported total time was still just the sum of each source's own elapsed time, which overcounts once sources run in parallel. Added a separate real wall-clock measurement around the whole run.

## What I Would Improve With More Time

- Implement the `type_coerced_count` signal from `SPEC.md`.
- Make the run deadline a hard cancellation, able to interrupt a single request already in flight, not just stop new ones from starting.
- Validate the generic engine design against a second, independently behaving API. Everything so far has only been tested against this one mock.
- Add an automated integration test that starts the mock server as a subprocess, instead of relying on manual verification.
