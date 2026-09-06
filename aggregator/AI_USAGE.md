# AI Usage

This document tracks how AI assistance (Claude Code) was used while building the
reliable data pipeline aggregator, per the requirements in `task.md`.

## Tools Used

- **Claude Code (Claude Sonnet 5)** — used interactively throughout the project for:
  - Understanding the challenge requirements and the mock API's behavior
  - Discussing architecture and language/framework tradeoffs
  - Drafting `SPEC.md` / `PLAN.md` content
  - Implementation of the aggregator, retry/rate-limit logic, and tests
  - Reviewing generated code before accepting it

## What Was Delegated to AI

- Initial read-through and summary of `task.md` and `mock-api-service/README.md`
  to confirm the mock service's contract (pagination styles, schema differences,
  retry/rate-limit behavior per source).
- Framework/language tradeoff discussion (Python vs Go, Flask vs Gin vs
  FastAPI/asyncio) for a concurrent, I/O-bound fan-out workload.
- (To be updated as implementation proceeds: code generation for HTTP client,
  normalization logic, retry/backoff, rate limiting, and test suite.)

## Feedback From Specification Review

- _(To be filled in once `SPEC.md`/`PLAN.md` are drafted and reviewed.)_

## Verification Process

- _(To be filled in as code is written — e.g., running `test_server.py` against
  the mock, writing unit tests for normalization/pagination/retry logic, manually
  inspecting a full run's output and summary.)_

## AI Suggestions Challenged, Rejected, or Independently Validated

- **Challenged**: Claude's first framing suggested picking between Flask and Gin
  as the aggregator's web framework. On review, this was pushed back on — the
  task doesn't require exposing an HTTP endpoint at all (per `task.md`'s explicit
  non-goals around unnecessary infrastructure), so a plain async Python script/CLI
  producing a report is sufficient and avoids unjustified complexity. Final
  direction: Python + asyncio (optionally FastAPI only if an HTTP interface is
  wanted), not Flask/Gin.

## Findings From Final AI Review

- _(To be filled in after a final AI-assisted review pass of the completed
  implementation.)_

## What I Would Improve With More Time

- _(To be filled in — e.g., additional retry/backoff tuning, more edge-case
  tests for malformed records, richer run-summary metrics.)_
