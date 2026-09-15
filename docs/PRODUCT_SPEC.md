# NorthForge Product Specification

## Product definition

NorthForge is a supervised AI workflow product. It lets users describe a business automation, inspect and modify the generated plan, run it with approved tools and evidence, review the execution, and improve future behavior through feedback and regression evaluation.

## Primary workflow

The first workflow is contract and policy review:

1. Retrieve relevant company policies and contract sections.
2. Extract specified fields such as renewal date, notice period, liability language, and governing terms.
3. Compare extracted terms with policy rules.
4. Identify exceptions and supporting evidence.
5. Draft a review summary with citations and uncertainty.
6. Pause for human review before any final disposition.

## Functional requirements

### Projects and workflows

Users can create projects, give them names and descriptions, and create workflow versions. Each workflow has a natural-language request, typed steps, tools, input sources, output schema, approval boundaries, and version metadata. Users can save, duplicate, inspect, edit, and restore workflow versions.

### Workflow proposal

The user enters a request. NorthForge proposes a validated workflow specification. The proposal must display assumptions, unsupported requests, required inputs, tools, approval points, and warnings. A proposal cannot run until the user approves it.

### Workflow editing

The MVP uses an editable step list or graph rather than a fully general canvas. Users can edit step names, step type, instructions, selected tools, required evidence, output schema, and approval requirements. Invalid edits must be rejected before execution.

### Run supervision

A run page shows status, current step, elapsed time, retrieved sources, model outputs, tool calls, validation results, errors, retries, and final output. Users can pause where supported, approve a gated step, retry a failed step, edit inputs, or rerun the workflow.

### Trust and evidence

Final material claims must link to source document and chunk identifiers. The interface must distinguish evidence, model interpretation, deterministic policy result, uncertainty, and human correction. If evidence is missing or conflicting, the system should abstain or request review.

### Feedback and evaluation

A reviewer can label a run as successful, incorrect, unsupported, unsafe, incomplete, or ambiguous, add notes, select a failure category, and promote the example to the evaluation set. Evaluation cases contain inputs, expected behavior, required evidence, allowed tools, and labels.

## Screens

- Project dashboard
- New workflow request
- Workflow proposal/editor
- Run history
- Live run and trace inspector
- Human review panel
- Evaluation cases
- Evaluation run results
- Settings and provider status

## MVP acceptance criteria

The complete journey works with synthetic data. The user can see and control the workflow before execution. A failed run is understandable and recoverable. A reviewer can convert a failure into a test. Evaluation results compare workflow versions using task-level metrics. No UI presents placeholders as completed functionality.

## Non-functional requirements

The product should be responsive on desktop, accessible by keyboard for core actions, explicit about loading and failure states, and safe by default. The API must be authenticated for project data. Provider credentials must never reach the browser.

## Scope controls

Use two or three read-only tools. Do not add arbitrary code execution, external side effects, broad integrations, or multi-agent collaboration unless a later decision record approves them. Do not expand to additional verticals before the primary workflow is polished and evaluated.
