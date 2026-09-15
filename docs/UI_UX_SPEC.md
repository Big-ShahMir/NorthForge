# NorthForge UI/UX Specification

## Experience principles

NorthForge should feel like a calm operations console for supervising AI work, not a novelty chatbot. The interface must make the workflow, evidence, uncertainty, and next human decision obvious. Use a desktop-first responsive layout, accessible controls, visible focus states, restrained animation, and clear status colors.

## Navigation

Use a persistent sidebar with: Projects, Workflows, Runs, Evaluations, and Settings. The top bar shows current project, provider status, and user menu. Every page needs a clear loading, empty, error, and unauthorized state.

## Core screens

### Project dashboard

Show project purpose, recent workflows, recent runs, evaluation summary, and a primary action to create a workflow. Surface failed or paused runs requiring attention.

### New workflow

A prominent text area accepts the user’s natural-language request. Include vertical context, available data sources, and a submit action. After planning, show warnings and assumptions before opening the editor.

### Workflow editor

Use a step list or graph with each node displaying label, type, tool, inputs, output schema, and approval state. A detail panel lets the user edit supported fields. Show validation errors inline. Include Save Draft, Validate, Approve Version, and Run controls with appropriate disabled states.

### Run inspector

Use a three-pane desktop layout: run timeline on the left, selected step details in the center, and final result/evidence panel on the right. Display status, duration, attempts, source citations, model output, tool arguments/results, validation checks, and errors. Include Retry Step, Edit and Rerun, Approve, Reject, Cancel, and Replay where valid.

### Human review

Present the pending decision, evidence, uncertainty, expected effect, and available choices. Approval must be explicit and recorded. Rejection must explain the next path. Never bury approval in a generic chat message.

### Evaluation dashboard

Show dataset/version, workflow version, run status, task success, groundedness, tool safety, latency, cost estimate, and failure categories. Compare two runs or versions. Link every metric to representative cases and raw traces.

## Visual language

Use a neutral dark or light workspace with one strong accent for active workflow state. Use green for verified success, amber for review/uncertainty, red for failure or blocked action, and blue/purple sparingly for model-generated content. Do not use color alone to communicate state; include labels and icons.

## Interaction rules

Disable actions while an operation is pending. Confirm destructive actions such as deleting a workflow or discarding edits. Preserve unsaved edits. Use toasts only for secondary confirmation; important errors remain visible near the affected component. Provide keyboard-accessible menus and dialogs.

## Data display

Long traces should be collapsible. Evidence cards show document name, chunk reference, excerpt, and relevance metadata. Model-generated text must be visually distinguished from deterministic checks and human corrections. Show raw JSON only behind an expandable technical view.

## MVP acceptance

A user can create a workflow, edit it, approve it, run it, inspect a failed step, retry or edit-and-rerun, approve a gated action, and promote feedback to an evaluation case. The interface never implies a model response is verified unless evidence and validation checks support that status.
