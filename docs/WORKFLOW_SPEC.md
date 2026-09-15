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

Every step has `id`, `type`, `label`, `inputs`, `output_schema`, `timeout_seconds`, `retry_policy`, `requires_approval`, and `failure_policy`. Inputs reference prior outputs or approved project data. Outputs are schema-validated before becoming available to later steps.

## Runtime state

State includes run identity, workflow version, current step, step outputs, evidence references, tool calls, warnings, approvals, retries, errors, and final result. State is checkpointed after each meaningful step and before/after human pauses.

## Planner behavior

The planner may propose only supported step types and registered tools. It must produce a structured workflow, identify assumptions, flag ambiguity, and refuse unsupported side effects. The proposal is not executable until schema validation and user approval succeed.

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
