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
- `POST /api/projects/{project_id}/workflows` — create a workflow from a manually supplied definition (version 1, draft). Phase 5 adds the natural-language planner job.
- `GET /api/workflows/{workflow_id}` — retrieve workflow and versions.
- `POST /api/workflows/{workflow_id}/versions` — save a new draft version.
- `GET /api/workflow-versions/{version_id}` — retrieve a version's definition, status, and validation warnings.
- `PATCH /api/workflow-versions/{version_id}` — overwrite a draft or validated version's definition (resets its status to draft). Returns `409 VERSION_IMMUTABLE` for an approved or archived version.
- `POST /api/workflow-versions/{version_id}/validate` — run semantic validation (`docs/WORKFLOW_SPEC.md`) against the version's definition and the live tool registry. On success, sets status `validated` and stores any warnings (`"<code>: <message>"` strings); on semantic errors returns `422 INVALID_WORKFLOW` and leaves status and stored warnings unchanged. `error.details` is a list of problem objects (`code`, `message`, `step_id`, `path`).
- `POST /api/workflow-versions/{version_id}/approve` — re-run the same semantic validation (the tool registry may have changed since the version was last validated) and refuse with `422 INVALID_WORKFLOW` under the same conditions as `validate`, then approve a validated version, making it immutable. Returns `409 VERSION_NOT_VALIDATED` if the version has not been validated.
- `POST /api/workflow-versions/{version_id}/restore` — create a new draft version copying an earlier version's definition verbatim.

### Catalog

- `GET /api/tools` — list every registered tool (`name`, `description`, `side_effect_class`, `access_scope`, `kind`, `input_schema`, `output_schema` as JSON Schema). No implementation details are exposed.
- `GET /api/workflow-step-types` — list every supported workflow step type (`type`, `title`, `description`, `config_schema`, `output_schema`, `allowed_tool_kinds`, `is_model_driven`).

### Documents and search (Phase 3, ADR-023–025)

Every route below is access-group filtered: a caller only ever sees documents, chunks, and rules whose `access_group` is a member of their own `access_groups_json` (see `docs/DATABASE_SPEC.md`, "Access groups"). Outside a caller's access groups, a document is `404 NOT_FOUND`, identical to a document that does not exist.

- `GET /api/projects/{project_id}/documents` — paginated (`limit`, `offset`; `data` is `{items, total, limit, offset}`), filterable by `document_type` and `vendor`. `404` if the project is not owned by the caller.
- `GET /api/documents/{document_id}` — document metadata plus a chunk list (`chunk_id`, `sequence`, `heading`, a 200-character `preview`, `token_count`) — never full chunk text.
- `GET /api/documents/{document_id}/chunks/{chunk_id}` — one chunk's full text and offsets.
- `POST /api/projects/{project_id}/documents/ingest` — body `{dataset_version}` (default `"v1"`); enqueues the `ingest_synthetic_dataset` worker job and returns `202 {job_id}` immediately. Ingestion itself is idempotent by content hash (see `docs/ARCHITECTURE.md`); calling this repeatedly is safe.
- `GET /api/jobs/{job_id}` — arq job status: `{status: queued | deferred | in_progress | complete | failed | not_found, result, error}`. `result` is only populated for a successful `complete` job; `error` is only the failing exception's class name (e.g. `"RuntimeError"`), never its message, so nothing sensitive from a job failure reaches the API response.
- `POST /api/projects/{project_id}/search` — body `{query, document_types?, vendor?, limit?}`; returns a `RetrievalOutcome` (`{status: ok | insufficient_evidence | conflicting_evidence, chunks, reason, total_candidates}`) scoped to the caller's access groups. This is the evidence-browsing endpoint a reviewer uses directly; the same retriever backs the `search_documents` tool.

### Users

- `GET /api/me` — `{id, subject, email, display_name, access_groups}` for the authenticated caller.

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

Use consistent envelopes where useful: `{data, error, request_id}`. Pagination uses cursor or limit/offset consistently: a list endpoint's `data` is `{items: [...], total, limit, offset}`. Run responses include status, timestamps, current step, and links to events. Never return API keys, internal stack traces, or unauthorized document content.

Every project-scoped and workflow-scoped route requires an authenticated caller and enforces ownership on every read. A request for a project, workflow, workflow version, or run owned by a different user returns `404 NOT_FOUND`, deliberately not `403 FORBIDDEN`, so a caller cannot distinguish "does not exist" from "exists but is not yours".

## Error codes

Use stable codes such as `UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `VALIDATION_ERROR`, `INVALID_WORKFLOW`, `VERSION_IMMUTABLE`, `VERSION_NOT_VALIDATED`, `VERSION_NOT_APPROVED`, `INVALID_RUN_TRANSITION`, `RUN_NOT_APPROVABLE`, `RUN_NOT_RETRYABLE`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `TOOL_BLOCKED`, and `INTERNAL_ERROR`.

## Async behavior

Planning, execution, ingestion, replay, and evaluation return accepted status plus an identifier. The UI polls or subscribes to events. Duplicate requests with the same idempotency key must not create duplicate runs.

## Contract discipline

Generate or manually maintain frontend types from the backend schema. Add API contract tests for every resource and authorization path. Update this document whenever an endpoint or response changes.
