# NorthForge Database Specification

## Database principles

PostgreSQL is the source of truth for product metadata, workflow definitions, runtime status, trace events, feedback, and evaluation results. Large files belong in object storage. Workflow versions and evaluation configurations are immutable after approval.

## Core tables

### `users`

`id`, `clerk_user_id` (unique; the Clerk `sub` claim in Clerk auth mode, or `dev|<X-Dev-User value>` in dev auth mode), `email` (nullable), `display_name` (nullable), `created_at`, `updated_at`. Rows are upserted by `clerk_user_id` on every authenticated request (`INSERT ... ON CONFLICT (clerk_user_id) DO UPDATE`), so a caller seen for the first time is created automatically; a new `email`/`display_name` overwrites the stored value, and a missing one never clobbers what is already stored. Never store provider credentials here.

### `projects`

`id`, `owner_id`, `name`, `description`, `vertical`, `created_at`, `updated_at`, `archived_at`.

### `workflows`

`id`, `project_id`, `name`, `description`, `current_version_id`, `created_by`, `created_at`, `updated_at`.

### `workflow_versions`

`id`, `workflow_id`, `version_number`, `definition_json`, `status`, `source_request`, `validation_warnings_json`, `model_config_json`, `created_by`, `created_at`, `approved_at`.

Statuses: `draft`, `validated`, `approved`, `archived`.

### `workflow_runs`

`id`, `workflow_version_id`, `project_id`, `status`, `input_json`, `result_json`, `error_json`, `checkpoint_ref`, `idempotency_key`, `started_at`, `completed_at`, `created_by`.

Statuses: `queued`, `running`, `paused`, `completed`, `failed`, `cancelled`. Allowed transitions: `queued` → `running` or `cancelled`; `running` → `paused`, `completed`, `failed`, or `cancelled`; `paused` → `running` or `cancelled`. `completed`, `failed`, and `cancelled` are terminal.

A run can only be created against an `approved` workflow version. `idempotency_key` is unique per `workflow_version_id` via a **partial** unique index (`WHERE idempotency_key IS NOT NULL`) rather than a plain unique constraint, so any number of runs may share a `NULL` key (no idempotency requested) while a repeated key for the same version returns the existing run instead of creating a duplicate.

### `step_runs`

`id`, `run_id`, `step_id`, `attempt`, `status`, `input_json`, `output_json`, `error_json`, `started_at`, `completed_at`.

Statuses: `pending`, `running`, `completed`, `failed`, `cancelled`. Unique on `(run_id, step_id, attempt)` so a retried step records a new row rather than overwriting the failed attempt.

### `trace_events`

`id`, `run_id`, `step_run_id`, `event_type`, `payload_json`, `sequence_number`, `created_at`. Events should be append-oriented and ordered per run.

### `feedback_labels`

`id`, `run_id`, `label`, `failure_category`, `notes`, `corrected_output_json`, `promoted_evaluation_case_id`, `created_by`, `created_at`.

### `evaluation_cases`

`id`, `project_id`, `name`, `input_json`, `expected_output_json`, `required_evidence_json`, `allowed_tools_json`, `labels_json`, `source_run_id`, `dataset_version`, `created_at`.

### `evaluation_runs`

`id`, `project_id`, `workflow_version_id`, `dataset_version`, `status`, `config_json`, `summary_json`, `started_at`, `completed_at`.

### `evaluation_results`

`id`, `evaluation_run_id`, `case_id`, `status`, `metrics_json`, `output_json`, `trace_ref`, `failure_category`, `created_at`.

## Relationships and authorization

Users own projects. Projects own workflows and evaluation cases. Workflow runs inherit project ownership. Every query must filter through the authenticated user’s project ownership or an explicit future membership table.

## Indexes

Index foreign keys, workflow status, run status and creation time, trace events by run and sequence, evaluation results by evaluation run, and dataset version. Add full-text or vector indexes only after measuring retrieval needs.

## JSON rules

Use JSON for versioned workflow definitions, flexible trace payloads, model configuration metadata, and evaluation metrics. Keep fields needed for filtering or joins as typed columns. Validate every JSON document at the application boundary.

Every JSON column uses PostgreSQL's `JSONB` type (via `sqlalchemy.dialects.postgresql.JSONB`), never plain `JSON` or a text column, so values are stored in a binary, indexable, whitespace-independent form. Columns that always hold a document rather than being optional (e.g. `definition_json`, `input_json`) are `NOT NULL` with a `'[]'::jsonb` or `'{}'::jsonb` `server_default` where an empty document is a valid starting state; columns that are genuinely absent until something happens (e.g. `result_json`, `output_json`) are nullable with no default.

## Status columns and CHECK constraints

Every status column is `String(32)` (never a native PostgreSQL `ENUM` type) with a `CheckConstraint` enumerating the allowed values, so adding a new status only requires a migration that adjusts the constraint rather than an `ALTER TYPE`. The Python-side allowed values live as `StrEnum`s next to the ORM models (`WorkflowVersionStatus`, `RunStatus`, `StepRunStatus`, `FeedbackLabelValue`, `EvaluationRunStatus`) so application code never compares against a bare string literal.

## Retention

MVP may retain synthetic run traces indefinitely. Add a documented retention mechanism before handling real data. Avoid raw prompt storage when a redacted or reference-based representation is sufficient.

## Migrations

Every schema change requires a migration, a clean-database test, and an update to this document. Never edit production schema manually without recording the change.
