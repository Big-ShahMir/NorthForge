# NorthForge Testing Strategy

## Testing objective

Tests must prove that NorthForge is safe, reproducible, inspectable, and useful. Model quality is evaluated separately from deterministic application correctness, but both are required.

## Unit tests

Cover workflow schema validation, step dependency checks, tool argument validation, permission filters, policy comparisons, citation construction, retry classification, idempotency, metric calculations, redaction, and failure taxonomy.

## Provider tests

Use a fake model provider for all normal tests. Test structured output success, malformed output, timeout, rate limit, unavailable model, retry, fallback, usage metadata, and cache behavior. Run a small NVIDIA smoke test only when credentials and quota are available.

## Runtime tests

Test planner generation and rejection of unsupported actions. Test runner success, validation failure, tool failure, timeout, retry exhaustion, checkpoint resume, cancellation, human approval pause/resume, and replay isolation. Kill or simulate worker interruption between meaningful nodes.

## API tests

Test authentication, project ownership, CRUD, validation errors, async job creation, idempotency, status transitions, event retrieval, approval/rejection, retry constraints, and malformed payloads. Ensure unauthorized records are never returned.

## Frontend tests

Test workflow proposal rendering, editing validation, run status transitions, trace display, approval controls, retry behavior, feedback promotion, loading/error/empty states, and keyboard-accessible dialogs. Use mocked API data for deterministic component tests.

## End-to-end tests

Use a local fake provider and synthetic seed data. Cover: create project -> plan workflow -> edit -> approve -> run -> inspect trace -> pause/approve -> complete -> label feedback -> promote case -> run evaluation.

## Evaluation tests

Run the versioned dataset through the evaluator. Assert schema validity, required metrics, trace references, failure categories, and report generation. Keep golden expected results for deterministic graders. Record variance for model-assisted grading.

## Security tests

Test cross-project access, secret redaction, prompt injection fixtures, unauthorized tool requests, malformed model outputs, request limits, duplicate run submissions, and unsafe approval bypasses.

## Load and failure tests

Use a small synthetic load to measure queue behavior, p50/p95 latency, worker throughput, retry recovery, and database connection behavior. Inject provider failures, tool failures, worker restarts, malformed outputs, and queue delays.

## Required commands

Document commands for formatting, linting, type checking, unit tests, integration tests, end-to-end tests, evaluation runs, and build. CI should run deterministic tests without requiring provider secrets.

## Completion standard

No phase is complete when only the happy path works. Each phase must include relevant failure tests and a concise report of untested assumptions.
