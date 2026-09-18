# NorthForge Workflow Specification

## Purpose

This document defines the portable workflow representation and runtime behavior. A workflow is a validated sequence or graph of typed steps. It is not an arbitrary prompt and cannot execute undeclared tools.

## Workflow object

A workflow contains `id`, `version`, `name`, `description`, `user_request`, `inputs`, `steps`, `edges`, `tools`, `output_schema`, `approval_points`, `policies`, `created_by`, and timestamps. Approved workflow versions are immutable.

## Supported step types

- `retrieve_documents`: retrieve permitted evidence using a query and filters.
- `extract_fields`: extract typed fields from selected evidence.
- `compare_policy`: apply deterministic rules and optionally model-assisted interpretation.
- `classify`: assign a typed category with evidence.
- `draft_summary`: produce a structured draft with citations.
- `human_review`: pause and await reviewer input.
- `validate_output`: enforce schema, citations, required fields, and policy rules.
- `finish`: persist the final result and summary metrics.

## Step contract

Every step has `id`, `type`, `label`, `instructions`, per-type configuration fields (see the table below), `timeout_seconds` (1-600, default 120), `retry_policy` (`max_attempts` 1-5, `backoff_seconds`, `backoff_multiplier`, `max_backoff_seconds`), `requires_approval`, and `failure_policy` (`fail_run` | `pause_for_review` | `skip`). Unknown fields on a step are rejected. Outputs are schema-validated before becoming available to later steps (`schemas/step_outputs.py`).

### Reference syntax

A per-type configuration field accepts either a literal value or a reference string that points at a declared workflow input or a prior step's output field (`schemas/refs.py`):

- `"$input.<name>"` refers to a declared workflow input named `<name>`.
- `"$step.<step_id>.<field>"` refers to the `<field>` output of the step identified by `<step_id>`.

Anything else, including non-string values, is a literal. A string starting with `$` that matches neither form is a malformed reference and is rejected. Semantic validation requires the referenced step to be an ancestor of the referencing step (via `edges`) and `field` to be a real top-level field of that step type's output model.

### Per-type configuration

| type | config fields | output fields |
|---|---|---|
| `retrieve_documents` | `tool` (`search_documents` or `get_document_chunk`, default `search_documents`), `query` (literal or ref, required), `document_types`, `vendor`, `limit` (1-20, default 8), `min_results` | `chunks: EvidenceChunk[]` |
| `extract_fields` | `evidence` (ref to a `chunks` field, required), `fields: FieldSpec[]` (`name`, `description`, `type`: string/text/date/number/boolean, `required`) (required, non-empty), `require_citations` | `fields: {name: ExtractedField}` |
| `compare_policy` | `fields` (ref, required), `policy_area` or a non-empty `rules` list (required), `tool` (`lookup_policy_rules`, default), `rules: DeterministicRule[]` (`rule_id`, `field`, `operator`, `value`, `severity`, `description`), `use_model_interpretation` | `results: PolicyCheckResult[]` |
| `classify` | `evidence` (ref, required), `categories` (at least two, required) | `category`, `confidence`, `evidence`, `rationale` |
| `draft_summary` | `sources` (list of refs, non-empty, required), `require_citations`, `max_words` | `summary_markdown`, `citations`, `uncertainties` |
| `human_review` | `show` (list of refs), `decisions` (default `["approve", "reject", "edit"]`) | `decision`, `reviewer_notes`, `edited_payload` |
| `validate_output` | `target` (ref, required), `checks` (subset of `schema`/`required_fields`/`citations`/`policy_results`) | `passed`, `checks: CheckOutcome[]` |
| `finish` | `result: {name: ref}` (every value must be a reference, non-empty, required) | `result: {name: value}` |

Every per-type field has a default, so a document that only sets the common fields still parses; the semantic layer (below) is what enforces the "required" fields in this table.

## Validation layers

Every workflow document passes through two layers (ADR-021):

- **Parse layer** (`schemas/workflow.py`, Pydantic, every read and write): shape, enums, the step id pattern, unique step ids, edges that reference real steps, an acyclic step graph, and at most one `finish` step. Every per-type field has a default, so any document that only sets the common fields still parses. Unknown fields on a step are rejected. Parse failures raise `INVALID_WORKFLOW` (422) with Pydantic's own error list as `details` and always block the write.
- **Semantic layer** (`schemas/workflow_validation.py`, run by the `validate` and `approve` endpoints, and non-blocking on every write): required per-type configuration, reference wiring between steps, tool declarations, and approval-point bookkeeping. Returns a report of `errors` and `warnings`, both lists of `Problem{code, message, step_id, path}`. Errors block `validate`/`approve` with `INVALID_WORKFLOW` (422, `details` is the list of problem objects) and leave the version's status and stored warnings untouched. Warnings never block; `validate` stores them as `"<code>: <message>"` strings on the version and sets its status to `validated`. A draft with semantic errors can still be saved (`POST`/`PATCH` only run the parse layer); only `validate` and `approve` enforce errors.

Problem codes:

| code | severity | meaning |
|---|---|---|
| `missing_required_config` | error | a step is missing a per-type field this spec marks required |
| `unknown_input_reference` | error | a `$input.<name>` reference names an input the workflow does not declare |
| `reference_not_ancestor` | error | a `$step.<id>.<field>` reference names a step that is not an ancestor of the referencing step via `edges` |
| `reference_unknown_field` | error | a `$step.<id>.<field>` reference names a field that step type's output model does not have |
| `invalid_reference_syntax` | error | a string starts with `$` but matches neither reference form |
| `tool_not_declared` | error | a step names a tool absent from the workflow's `tools` list |
| `tool_not_registered` | error | a declared or step-level tool name is not in the running tool registry (`GET /api/tools`) |
| `tool_not_allowed_for_step` | error | a step names a tool outside the kinds allowed for its type (e.g. only `lookup_policy_rules` for `compare_policy`) |
| `approval_point_mismatch` | error | `approval_points` is not exactly the steps with `requires_approval=true` plus every `human_review` step |
| `no_finish_step` | error | the workflow has no `finish` step |
| `unreachable_step` | error | a step is not reachable by walking forward from a root step |
| `finish_result_reference_invalid` | error | a `finish.result` value is not a reference (every result value must reference a prior step's output) |
| `no_human_review` | warning | the workflow has no `human_review` step |
| `no_retrieval_before_extraction` | warning | an `extract_fields` step has no `retrieve_documents` ancestor |
| `draft_without_citations` | warning | a `draft_summary` step sets `require_citations=false` |
| `high_retry_budget` | warning | a step's `max_attempts x timeout_seconds` exceeds 20 minutes |

`known_tools` (the registry's tool names) is optional in `validate_workflow`; passing `None` skips `tool_not_registered` entirely. Drafts are validated this way (they must not depend on the tool registry); `validate`/`approve` always pass the real registry's names.

## Failure policy semantics

`failure_policy` governs what happens when a step exhausts its `retry_policy` without succeeding:

- `fail_run` (default): the run transitions to `failed`; downstream steps do not execute.
- `pause_for_review`: the run pauses and surfaces the failure for a human decision, the same way an approval point does, rather than terminating outright.
- `skip`: the step is marked failed but the run continues to steps that do not depend on its output.

`retry_policy` applies bounded exponential backoff (`backoff_seconds * backoff_multiplier ** attempt`, capped at `max_backoff_seconds`) to each retryable attempt, up to `max_attempts`. Only tool failures the runtime classifies as retryable (`ToolExecutionError(retryable=True)`) are retried; malformed arguments, blocked tools, and invalid tool output are not.

## Runtime state

State includes run identity, workflow version, current step, step outputs, evidence references, tool calls, warnings, approvals, retries, errors, and final result. State is checkpointed after each meaningful step and before/after human pauses.

## Planner behavior

The planner may propose only supported step types and registered tools. It must produce a structured workflow, identify assumptions, flag ambiguity, and refuse unsupported side effects. The proposal is not executable until schema validation and user approval succeed.

Implementation (Phase 5, `northforge/planner/`, ADR-028): the model returns a flat proposal (steps with a per-type `config` object, edges, inputs, tools, assumptions, clarifying questions, rejected actions) and code compiles it into a definition. The compiler guarantees, regardless of model output: only registered tools are declared (others become `rejected_actions` with category `unsupported_tool`); `policies` is empty; `approval_points` equals the steps with `requires_approval` plus every `human_review` step; a `human_review` step is inserted before `finish` when the model omitted one, and that insertion is listed under `assumptions`; the result passes the parse layer and the semantic layer above with the live tool registry. Validation problems are sent back to the model once; problems remaining after that repair turn are stored on the version (`planner_output.validation_errors`) and the version stays `draft`, so `validate` and `approve` block until the user edits it. A deterministic screen records side-effect requests (send, pay, sign, delete, amend, upload, auto-approve, and similar) and prompt-injection markers as rejections before the model is called. Tool output gathered while planning is passed to the model as labelled, untrusted data and can only inform the proposal, never change the rules.

## Tool policy

Tools are registered with a name, description, input schema, output schema, access scope, side-effect class, and implementation. MVP tools are read-only or draft-only. Tool arguments are validated before invocation. Tool outputs are treated as untrusted data and cannot alter system policies.

## Approval behavior

A workflow can pause at configured approval points. The UI receives the proposed action, evidence, uncertainty, and expected effect. Approval, rejection, or edit decisions are recorded with user and timestamp. Rejection terminates or routes to a defined fallback; it must not silently continue.

## Failure behavior

Failures have category, message, retryability, step, provider/tool context, trace ID, and user-safe description. Retryable failures use bounded exponential backoff. Non-retryable failures pause or terminate according to policy. Unsupported evidence should produce abstention or review, not fabricated certainty.

## Replay behavior

A replay references an immutable workflow version, input fixture, and model configuration. It records outputs and metrics without changing the original run. Side-effecting tools are disabled in replay.

## Serialization

Workflow JSON must be versioned, validated on read and write, stable enough for diffs, and independent of Python object serialization. Do not persist secrets, raw provider credentials, or unnecessary sensitive prompt content.

## Example flow

```text
retrieve_documents -> extract_fields -> compare_policy -> draft_summary
                                                   -> human_review
                                                   -> validate_output -> finish
```

## Invariants

A run references exactly one workflow version. A step cannot consume an undeclared input. A tool cannot be called without a registered contract. A final result cannot be successful without required fields and evidence checks. All pauses, retries, approvals, and failures are traceable.
