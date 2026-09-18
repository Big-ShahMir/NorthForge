# NorthForge Database Specification

## Database principles

PostgreSQL is the source of truth for product metadata, workflow definitions, runtime status, trace events, feedback, and evaluation results. Large files belong in object storage. Workflow versions and evaluation configurations are immutable after approval.

## Core tables

### `users`

`id`, `clerk_user_id` (unique; the Clerk `sub` claim in Clerk auth mode, or `dev|<X-Dev-User value>` in dev auth mode), `email` (nullable), `display_name` (nullable), `access_groups_json` (JSONB, `NOT NULL DEFAULT '["procurement"]'`), `created_at`, `updated_at`. Rows are upserted by `clerk_user_id` on every authenticated request (`INSERT ... ON CONFLICT (clerk_user_id) DO UPDATE`), so a caller seen for the first time is created automatically; a new `email`/`display_name` overwrites the stored value, and a missing one never clobbers what is already stored. Never store provider credentials here.

`access_groups_json` is a list of access group names (`procurement`, `legal_restricted`, `hr_restricted`; see "Access groups" below). Every user starts with `["procurement"]` only; additional groups are granted out of band (locally, `python -m northforge.ingestion.seed --grant-user <subject> <group>`).

### `projects`

`id`, `owner_id`, `name`, `description`, `vertical`, `created_at`, `updated_at`, `archived_at`.

### `workflows`

`id`, `project_id`, `name`, `description`, `current_version_id`, `created_by`, `created_at`, `updated_at`.

### `workflow_versions`

`id`, `workflow_id`, `version_number`, `definition_json`, `status`, `source_request`, `validation_warnings_json`, `model_config_json`, `planner_output_json` (planner assumptions, questions, rejected actions, remaining validation problems, and trace-safe model/tool records for versions created by the Phase 5 planner; `{}` otherwise), `created_by`, `created_at`, `approved_at`.

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

### `documents`

`id`, `project_id`, `external_id` (str(200); the dataset's stable id, e.g. `doc_acme_cloud_msa`), `name`, `document_type` (`msa`, `dpa`, `sow`, `nda`, `order_form`, `policy`), `vendor` (nullable), `effective_date` (nullable date), `expires_at` (nullable date), `access_group`, `metadata_json` (JSONB; carries `document_family`, `policy_area`, `supersedes`, and other dataset-specific fields), `content_hash` (sha256 of the raw document content, used by ingestion to skip unchanged documents), `storage_key` (nullable; the object storage key holding the raw markdown, `projects/<project_id>/documents/<external_id>.md`), `dataset_version`, `chunk_count`, `created_at`, `updated_at`. Unique on `(project_id, external_id)`. Indexed on `(project_id, document_type)` and `(project_id, vendor)`.

### `document_chunks`

`id`, `document_id`, `chunk_id` (str(32), sequential per document: `c01`, `c02`, ...), `sequence`, `heading` (nullable; the `##` heading the chunk falls under), `text`, `start_offset`/`end_offset` (into the normalized document content), `token_count`, `content_hash` (sha256 of the chunk's own normalized text), `metadata_json`, `search_vector` (a PostgreSQL-generated stored column, `to_tsvector('english', coalesce(heading,'') || ' ' || text)`, never written from application code), `created_at`. Unique on `(document_id, chunk_id)`. A GIN index on `search_vector` backs full-text search; an index on `content_hash` backs near-duplicate detection.

### `policy_rules`

`id`, `project_id`, `rule_id` (str(100), stable across dataset versions), `policy_document_id` (FK `documents`), `chunk_id` (the chunk of that document stating the rule; resolved at ingestion time from the dataset's `section` heading), `policy_area`, `condition`, `requirement`, `severity` (`low`, `medium`, `high`). Unique on `(project_id, rule_id)`. A rule's access group is inherited from `policy_document_id`'s document, not stored redundantly.

## Access groups

Every `documents` row (and by inheritance every `document_chunks` and `policy_rules` row) belongs to exactly one access group: `procurement` (the default for every user), `legal_restricted`, or `hr_restricted`. A caller only ever sees rows whose `access_group` is a member of their own `access_groups_json`; a restricted document is filtered out in the SQL `WHERE` clause of every read (the documents API, the search API, and the `search_documents`/`get_document_chunk`/`lookup_policy_rules` tools), never in Python after the fact, so a caller without a group cannot distinguish "does not exist" from "exists but is restricted".

## Relationships and authorization

Users own projects. Projects own workflows, evaluation cases, and (Phase 3) documents and policy rules. Workflow runs inherit project ownership. Every query must filter through the authenticated user's project ownership (and, for documents/chunks/rules, the caller's access groups) or an explicit future membership table.

## Indexes

Index foreign keys, workflow status, run status and creation time, trace events by run and sequence, evaluation results by evaluation run, and dataset version. `document_chunks.search_vector` has a GIN index for full-text search; `documents` is indexed on `(project_id, document_type)` and `(project_id, vendor)` for the documents-list and search filters.

## JSON rules

Use JSON for versioned workflow definitions, flexible trace payloads, model configuration metadata, and evaluation metrics. Keep fields needed for filtering or joins as typed columns. Validate every JSON document at the application boundary.

Every JSON column uses PostgreSQL's `JSONB` type (via `sqlalchemy.dialects.postgresql.JSONB`), never plain `JSON` or a text column, so values are stored in a binary, indexable, whitespace-independent form. Columns that always hold a document rather than being optional (e.g. `definition_json`, `input_json`) are `NOT NULL` with a `'[]'::jsonb` or `'{}'::jsonb` `server_default` where an empty document is a valid starting state; columns that are genuinely absent until something happens (e.g. `result_json`, `output_json`) are nullable with no default.

## Status columns and CHECK constraints

Every status column is `String(32)` (never a native PostgreSQL `ENUM` type) with a `CheckConstraint` enumerating the allowed values, so adding a new status only requires a migration that adjusts the constraint rather than an `ALTER TYPE`. The Python-side allowed values live as `StrEnum`s next to the ORM models (`WorkflowVersionStatus`, `RunStatus`, `StepRunStatus`, `FeedbackLabelValue`, `EvaluationRunStatus`) so application code never compares against a bare string literal.

## Retention

MVP may retain synthetic run traces indefinitely. Add a documented retention mechanism before handling real data. Avoid raw prompt storage when a redacted or reference-based representation is sufficient.

## Migrations

Every schema change requires a migration, a clean-database test, and an update to this document. Never edit production schema manually without recording the change.
