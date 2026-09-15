# NorthForge Database Specification

## Database principles

PostgreSQL is the source of truth for product metadata, workflow definitions, runtime status, trace events, feedback, and evaluation results. Large files belong in object storage. Workflow versions and evaluation configurations are immutable after approval.

## Core tables

### `users`

Stores application identity and timestamps. Use the existing authentication integration where available. Never store provider credentials here.

### `projects`

`id`, `owner_id`, `name`, `description`, `vertical`, `created_at`, `updated_at`, `archived_at`.

### `workflows`

`id`, `project_id`, `name`, `description`, `current_version_id`, `created_by`, `created_at`, `updated_at`.

### `workflow_versions`

`id`, `workflow_id`, `version_number`, `definition_json`, `status`, `source_request`, `validation_warnings_json`, `model_config_json`, `created_by`, `created_at`, `approved_at`.

Statuses: `draft`, `validated`, `approved`, `archived`.

### `workflow_runs`

`id`, `workflow_version_id`, `project_id`, `status`, `input_json`, `result_json`, `error_json`, `checkpoint_ref`, `idempotency_key`, `started_at`, `completed_at`, `created_by`.

Statuses: `queued`, `running`, `paused`, `completed`, `failed`, `cancelled`.

### `step_runs`

`id`, `run_id`, `step_id`, `attempt`, `status`, `input_json`, `output_json`, `error_json`, `started_at`, `completed_at`.

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

## Retention

MVP may retain synthetic run traces indefinitely. Add a documented retention mechanism before handling real data. Avoid raw prompt storage when a redacted or reference-based representation is sufficient.

## Migrations

Every schema change requires a migration, a clean-database test, and an update to this document. Never edit production schema manually without recording the change.
