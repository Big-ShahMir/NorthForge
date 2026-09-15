# NorthForge API Specification

## API principles

Use FastAPI with Pydantic schemas. All project data endpoints require authentication and project authorization. Long-running operations return a job/run identifier rather than blocking the request. API errors use stable codes and user-safe messages.

## Core resources

`Project`, `Workflow`, `WorkflowVersion`, `WorkflowRun`, `TraceEvent`, `FeedbackLabel`, `EvaluationCase`, `EvaluationRun`, and `ProviderStatus`.

## Endpoints

### Projects

- `GET /api/projects` — list authorized projects.
- `POST /api/projects` — create a project.
- `GET /api/projects/{project_id}` — retrieve project summary.
- `PATCH /api/projects/{project_id}` — update name/description.

### Workflows

- `GET /api/projects/{project_id}/workflows` — list workflows.
- `POST /api/projects/{project_id}/workflows/plan` — create a planner job from natural-language request.
- `GET /api/workflows/{workflow_id}` — retrieve workflow and versions.
- `POST /api/workflows/{workflow_id}/versions` — save a draft version.
- `POST /api/workflow-versions/{version_id}/validate` — validate definition.
- `POST /api/workflow-versions/{version_id}/approve` — approve immutable version.

### Runs

- `POST /api/workflow-versions/{version_id}/runs` — enqueue a run with input and idempotency key.
- `GET /api/runs/{run_id}` — retrieve status and summary.
- `GET /api/runs/{run_id}/events` — list or stream trace events.
- `POST /api/runs/{run_id}/pause` — request pause if supported.
- `POST /api/runs/{run_id}/resume` — resume a paused run.
- `POST /api/runs/{run_id}/approve` — record human approval.
- `POST /api/runs/{run_id}/reject` — reject approval request.
- `POST /api/runs/{run_id}/retry` — retry a safe failed step/run.
- `POST /api/runs/{run_id}/replay` — create an isolated replay.
- `POST /api/runs/{run_id}/cancel` — cancel a queued/running run.

### Feedback and evaluations

- `POST /api/runs/{run_id}/feedback` — label a run and optionally promote it.
- `GET /api/projects/{project_id}/evaluation-cases` — list cases.
- `POST /api/projects/{project_id}/evaluation-cases` — create a case.
- `POST /api/workflow-versions/{version_id}/evaluations` — enqueue evaluation.
- `GET /api/evaluations/{evaluation_id}` — retrieve summary.
- `GET /api/evaluations/{evaluation_id}/results` — retrieve case results.

### System

- `GET /health` — liveness.
- `GET /ready` — dependency readiness.
- `GET /api/provider-status` — safe provider availability summary without secrets.

## Response rules

Use consistent envelopes where useful: `{data, error, request_id}`. Pagination uses cursor or limit/offset consistently. Run responses include status, timestamps, current step, and links to events. Never return API keys, internal stack traces, or unauthorized document content.

## Error codes

Use stable codes such as `UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `VALIDATION_ERROR`, `INVALID_WORKFLOW`, `RUN_NOT_APPROVABLE`, `RUN_NOT_RETRYABLE`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `TOOL_BLOCKED`, and `INTERNAL_ERROR`.

## Async behavior

Planning, execution, ingestion, replay, and evaluation return accepted status plus an identifier. The UI polls or subscribes to events. Duplicate requests with the same idempotency key must not create duplicate runs.

## Contract discipline

Generate or manually maintain frontend types from the backend schema. Add API contract tests for every resource and authorization path. Update this document whenever an endpoint or response changes.
