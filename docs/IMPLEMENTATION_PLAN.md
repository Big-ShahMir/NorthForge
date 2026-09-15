# NorthForge Implementation Plan

## 1. Purpose

This document is the execution plan for building **NorthForge**, a web application for creating, running, supervising, and improving reliable AI workflows.

The initial product vertical is **vendor contract and policy review** using synthetic or explicitly reusable documents. The application will let a user describe an automation in natural language, edit the proposed workflow, run it against controlled data, inspect evidence and intermediate steps, intervene when necessary, and convert failures into regression tests.

The implementation should optimize for a **focused, polished, demonstrable MVP** rather than a general-purpose agent platform.

## 2. MVP outcome

At the end of the MVP, a user must be able to:

1. Create a project.
2. Describe a contract-review automation in natural language.
3. Receive a typed workflow proposal.
4. Inspect and edit the workflow before execution.
5. Run the workflow against synthetic documents.
6. Observe each workflow step, tool call, source, output, duration, and error.
7. Pause for human review or approval at a defined boundary.
8. Retry, edit, or rerun a failed step or complete workflow.
9. Receive a structured, evidence-backed result.
10. Label a failure and add it to a versioned evaluation set.
11. Run the evaluation set and compare baseline versus improved results.

## 3. Technical baseline

Use the following stack unless a documented decision changes it:

- **Frontend:** React, TypeScript, React Flow, Tailwind CSS, and shadcn/ui.
- **Backend:** Python, FastAPI, and Pydantic.
- **Orchestration:** LangGraph for stateful workflow graphs; use LangChain selectively for model, retrieval, and tool integrations.
- **Models:** NVIDIA API Catalog/NIM through an OpenAI-compatible API, accessed only from the backend.
- **Model routing:** A provider abstraction that supports model assignment by task, fallbacks, retries, caching, capability checks, and per-model metrics.
- **Database:** PostgreSQL.
- **Retrieval:** PostgreSQL with pgvector if practical; otherwise use a clearly isolated vector-store adapter.
- **Async jobs:** Redis-backed queue and a separate worker process.
- **Object storage:** S3-compatible storage for documents and generated artifacts.
- **Observability:** Structured trace events and application metrics; optionally LangSmith during development.
- **Local development:** Docker Compose where useful.
- **Deployment target:** Render web service, background worker, managed Postgres, and queue.

Do not introduce additional infrastructure merely for architectural completeness. Every dependency must support a visible MVP capability.

## 4. Execution rules for Claude Code

Claude Code should work phase by phase and must not silently skip verification.

At the beginning of each phase, Claude should:

- Read this file and the relevant product, architecture, workflow, database, UI, evaluation, and security specifications.
- Inspect the current repository state.
- State the phase objective and files it expects to change.
- Identify unresolved assumptions that could materially affect the implementation.

At the end of each phase, Claude must:

- Run the relevant tests, type checks, linting, and build commands.
- Start the application when applicable and verify the affected user flow.
- Report what works, what is incomplete, known limitations, and exact commands used.
- Update `README.md`, `DECISIONS.md`, and relevant documentation when behavior or architecture changes.
- Stop at the checkpoint unless explicitly instructed to continue.

Claude may make reasonable low-risk implementation decisions autonomously. It must pause before:

- Changing the core architecture or technology stack.
- Expanding the MVP vertical or adding major feature areas.
- Adding a paid service or an unnecessary external dependency.
- Publishing or processing real sensitive data.
- Making a claim that the system is secure, compliant, or production-ready.
- Removing a feature required by the MVP outcome.

## 5. Definition of done for every phase

A phase is complete only when:

- The intended functionality is implemented rather than represented by a placeholder.
- The code is organized into clear modules with typed interfaces.
- Happy paths and important failure paths are tested.
- Errors are surfaced clearly to users and logs.
- Secrets are read from environment variables and never committed.
- Documentation reflects the current implementation.
- The phase acceptance criteria are demonstrably satisfied.

## 6. Development phases

### Phase 0 — Repository inspection and project bootstrap

**Goal:** Establish a clean, reproducible development foundation.

**Tasks:**

- Inspect the repository and existing tooling before changing files.
- Create or confirm the repository structure.
- Configure frontend, backend, worker, shared schemas, tests, formatting, linting, and environment handling.
- Add `.env.example` without real credentials.
- Add Docker Compose for local Postgres and queue services if needed.
- Add a basic health endpoint and a minimal frontend shell.
- Create the initial `README.md` with setup and verification commands.

**Acceptance criteria:**

- A new developer can install dependencies and start the application using documented commands.
- Frontend, API, database connection, and worker health checks are independently verifiable.
- CI or local scripts run formatting, linting, type checks, and baseline tests.

**Checkpoint:** Confirm the repository starts cleanly before implementing product behavior.

### Phase 1 — Domain model and database foundation

**Goal:** Create durable data structures for users, projects, workflows, runs, traces, feedback, and evaluation cases.

**Tasks:**

- Define database entities and relationships.
- Create migration scripts.
- Implement repositories or data-access helpers.
- Add project and workflow CRUD operations.
- Define ownership boundaries so users cannot access another user’s project data.
- Store workflow definitions as versioned JSON with schema validation.
- Store run status, timestamps, errors, and checkpoint references.

**Minimum entities:**

- User
- Project
- Workflow
- WorkflowVersion
- WorkflowRun
- WorkflowStepRun
- TraceEvent
- FeedbackLabel
- EvaluationCase
- EvaluationRun
- EvaluationResult

**Acceptance criteria:**

- Migrations run successfully on a clean database.
- CRUD tests cover valid, invalid, and unauthorized access.
- A workflow version can be created, retrieved, updated, and restored.
- Run and trace records can be persisted independently of the UI.

**Checkpoint:** Review the schema and confirm that it supports replay, feedback, and evaluation without redesign.

### Phase 2 — Workflow schema and deterministic tools

**Goal:** Define the controlled language of NorthForge workflows.

**Tasks:**

- Create Pydantic/TypeScript schemas for workflow definitions.
- Define supported step types, including:
  - Retrieve documents
  - Extract structured fields
  - Compare against policy
  - Classify or route
  - Draft a report
  - Request human approval
  - Finish with a structured result
- Define step inputs, outputs, dependencies, timeouts, retry policies, and approval requirements.
- Implement two or three mocked read-only tools.
- Validate tool arguments and tool permissions.
- Create deterministic fixtures for tool success and failure.

**Acceptance criteria:**

- Invalid workflow definitions are rejected with useful errors.
- Tools have typed contracts and deterministic test doubles.
- Tool calls cannot access undeclared data or perform undeclared actions.
- Workflow versions are portable JSON documents.

**Checkpoint:** Confirm that a workflow can be represented clearly without model-specific assumptions.

### Phase 3 — Synthetic data and retrieval

**Goal:** Build the controlled enterprise-data environment for the MVP.

**Tasks:**

- Generate or curate 50–100 synthetic contracts, policies, and vendor records.
- Add metadata such as document type, vendor, effective date, and access group.
- Create ingestion, chunking, indexing, and retrieval code.
- Preserve document and chunk identifiers for citations.
- Implement simulated document-level access filtering.
- Add retrieval evaluation fixtures with known relevant passages.
- Add an abstention path when evidence is missing or conflicting.

**Acceptance criteria:**

- Synthetic data can be regenerated deterministically.
- Retrieval returns source identifiers and text spans.
- Access filters are applied before results reach the model.
- The system can cite the source of every material claim in a final result.
- Retrieval tests cover misses, duplicates, conflicting policies, and unauthorized documents.

**Checkpoint:** Review data licensing, synthetic-data boundaries, and retrieval quality before building the agent runtime.

### Phase 4 — NVIDIA model provider and model router

**Goal:** Integrate NVIDIA-hosted models without coupling the application to one model.

**Tasks:**

- Implement a backend-only NVIDIA provider adapter.
- Support structured outputs, tool-calling where available, timeouts, retries, and error normalization.
- Create a model capability registry covering structured output, tool use, context length, and expected latency.
- Implement model routing by task:
  - Workflow planning
  - Extraction/classification
  - Draft generation
  - Evaluation assistance
- Define and validate the complete role configuration: `NVIDIA_MODEL_PLANNER`, `NVIDIA_MODEL_EXTRACTION`, `NVIDIA_MODEL_DRAFTER`, `NVIDIA_MODEL_EVALUATOR`, `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`, and `RERANKER_PROVIDER`/`RERANKER_MODEL`.
- Implement separate embedding and reranking provider interfaces; allow local implementations when hosted endpoints are unavailable or quota-constrained.
- Record the selected provider and model for every generation, embedding, and reranking operation.
- Add per-model request logging without storing secrets or unnecessary sensitive prompts.
- Add caching for deterministic development and evaluation requests where appropriate.
- Add a mock provider for tests.
- Add graceful fallback behavior when a model is unavailable or rate-limited.

**Acceptance criteria:**

- The application can complete a mocked model flow without external access.
- NVIDIA credentials are loaded only from environment variables.
- Provider errors become user-visible, actionable application errors.
- The active model and provider are recorded in run metadata.
- All six routing roles are configurable and covered by startup validation or an explicit local-provider fallback.
- Rate-limit and timeout behavior is tested.

**Checkpoint:** Verify actual NVIDIA connectivity separately from the rest of the application and document model limitations.

### Phase 5 — Workflow planner graph

**Goal:** Convert a natural-language automation request into an editable, validated workflow proposal.

**Tasks:**

- Build a LangGraph planner graph.
- Use the model to propose workflow steps, tools, inputs, outputs, and approval points.
- Validate the result against the workflow schema.
- Reject unsupported tools and unsafe or ambiguous actions.
- Provide clarification or a safe fallback when the request is underspecified.
- Store the original request, planner model, proposed workflow, and validation warnings.
- Add planner fixtures for common, ambiguous, and malicious requests.

**Acceptance criteria:**

- A natural-language request produces a valid workflow proposal for the target vertical.
- Unsupported actions do not silently become executable steps.
- The proposal explains assumptions and requests clarification when necessary.
- Planner behavior is covered by schema-validation and fixture tests.

**Checkpoint:** Test several requests manually and confirm that the generated workflows are understandable to a non-expert user.

### Phase 6 — Workflow execution graph

**Goal:** Execute approved workflows reliably and observably.

**Tasks:**

- Build a separate LangGraph runner for approved workflow definitions.
- Persist state at meaningful step boundaries.
- Implement deterministic nodes for retrieval, validation, policy checks, and formatting.
- Implement model-driven nodes only where judgment is needed.
- Add retries with bounded attempts and categorized errors.
- Add timeouts, cancellation, and safe failure behavior.
- Add checkpointed resume after worker interruption.
- Emit trace events for every step and tool call.
- Support pause/resume for human approval.
- Ensure reruns do not duplicate side effects; the MVP should use read-only tools and drafts only.

**Acceptance criteria:**

- A complete contract/policy review workflow runs end to end.
- A failed worker can resume from a checkpoint.
- A human approval pause can be resumed without losing state.
- Every step has an inspectable status and trace.
- Failure states are explicit rather than being represented as successful text output.

**Checkpoint:** Kill and restart the worker during a run and verify recovery.

### Phase 7 — Workflow builder and supervision interface

**Goal:** Make the runtime understandable and controllable through the product UI.

**Tasks:**

- Build project and workflow navigation.
- Build the natural-language workflow creation screen.
- Build an editable step-list or graph view.
- Display step types, tools, inputs, outputs, approval points, and warnings.
- Add workflow versioning and save/restore behavior.
- Build the run screen with streaming status where practical.
- Build a trace inspector showing:
  - Inputs and context references
  - Retrieved evidence
  - Model output
  - Tool calls and results
  - Validation checks
  - Duration and status
  - Errors and retry controls
- Build human review and approval controls.
- Build edit-and-rerun and run-replay controls.
- Handle loading, empty, error, unauthorized, and partial-run states.

**Acceptance criteria:**

- A user can create, inspect, edit, approve, run, and replay a workflow without direct database access.
- The UI distinguishes proposed, running, paused, failed, completed, and cancelled states.
- The trace view explains what happened without exposing internal secrets.
- The product remains usable on common desktop screen sizes.

**Checkpoint:** Conduct a complete manual walkthrough using a fresh project and record usability issues.

### Phase 8 — Evaluation and feedback-to-regression loop

**Goal:** Demonstrate that NorthForge improves from observed failures.

**Tasks:**

- Create 25–50 labeled evaluation cases.
- Define expected outputs, required evidence, allowed tools, and failure labels.
- Implement deterministic graders for schema validity, required fields, evidence presence, policy decisions, and tool compliance.
- Add model-assisted grading only where deterministic grading is insufficient.
- Track task success, groundedness, step correctness, invalid tool calls, approval compliance, latency, token usage, and estimated cost.
- Build replay evaluation against a selected workflow version.
- Add a failure taxonomy:
  - Retrieval miss
  - Bad context
  - Unsupported claim
  - Wrong tool
  - Malformed tool arguments
  - State loss
  - Ambiguous intent
  - Evaluator disagreement
- Build a feedback screen where a reviewer labels a run and promotes it to an evaluation case.
- Produce baseline-versus-improved reports.

**Acceptance criteria:**

- Evaluation runs are repeatable and versioned.
- Raw traces are retained for failed cases.
- A user correction can become a regression case.
- At least three failure-driven changes produce measurable before/after results.
- Evaluation limitations and grader uncertainty are documented.

**Checkpoint:** Review whether the metrics measure user workflow success rather than only model text similarity.

### Phase 9 — Security, safety, and reliability hardening

**Goal:** Make the MVP safe to demonstrate and honest about its limits.

**Tasks:**

- Verify authentication and project-level authorization.
- Validate all workflow, tool, and model inputs.
- Keep provider keys and storage credentials server-side.
- Add request size and rate limits where appropriate.
- Add prompt-injection fixtures for retrieved documents.
- Separate trusted instructions from untrusted document content.
- Require approval before any consequential action; keep MVP tools read-only or draft-only.
- Add audit events for approvals, edits, retries, and workflow version changes.
- Redact secrets and unnecessary sensitive content from traces.
- Add failure injection for provider errors, tool errors, worker termination, malformed outputs, and database interruptions.
- Document that synthetic permissions do not prove production security or compliance.

**Acceptance criteria:**

- Unauthorized project access is blocked and tested.
- Malformed model outputs fail safely.
- Retrieved instructions cannot silently override system policies.
- Secrets do not appear in source control, logs, or client bundles.
- Recovery and approval behavior is documented.

**Checkpoint:** Perform a security review before public deployment.

### Phase 10 — Deployment and operations

**Goal:** Deploy a reproducible public demo environment.

**Tasks:**

- Create Dockerfiles or deployment configuration for the web service and worker.
- Configure Render web service, worker, Postgres, and queue.
- Configure environment variables through the hosting platform.
- Run database migrations during deployment using a safe procedure.
- Configure health checks and worker liveness checks.
- Configure structured logs and basic application metrics.
- Add seed data and a demo project that can be recreated.
- Add safe error pages and provider-unavailable behavior.
- Document deployment, rollback, data reset, and local reproduction.
- Confirm the deployed app does not expose API credentials.

**Acceptance criteria:**

- The deployed web app can create and execute a demo workflow.
- The worker processes asynchronous runs independently of web requests.
- The database and queue reconnect after service restarts.
- The public demo has clear limitations and no real sensitive data.
- A new deployment can be reproduced from the repository documentation.

**Checkpoint:** Perform a clean deployment from the documented process rather than relying on undocumented dashboard state.

### Phase 11 — Portfolio polish and final evidence

**Goal:** Make the project immediately understandable to an AI-engineering hiring manager.

**Tasks:**

- Add a short product demo or screen recording.
- Add an architecture diagram.
- Add an evaluation report with baseline and improved results.
- Add three failure postmortems showing what changed and why.
- Add a concise operations note covering state, retries, traces, and deployment.
- Add reproducible commands for local setup, tests, data generation, and evaluation.
- Add contribution guidance and issue templates if open-sourcing.
- Review naming, UI consistency, loading/error states, and accessibility.
- Remove dead code, misleading claims, and placeholder features.
- Tag a stable MVP release.

**Acceptance criteria:**

- A reviewer can understand the product, architecture, and results within five minutes.
- The demo shows inspection, intervention, recovery, and measurable improvement.
- The repository is runnable by another engineer.
- Limitations are explicit and claims are supported by evidence.

**Final checkpoint:** Conduct a fresh-user review using only the README and public deployment.

## 7. Cross-phase quality requirements

Every phase must preserve the following:

- **Typed boundaries:** schemas for API payloads, workflows, tools, traces, and evaluation results.
- **Deterministic core:** use ordinary code for validation, permissions, routing, calculations, and state transitions where possible.
- **Model isolation:** all provider-specific behavior belongs behind an adapter.
- **Observability:** every workflow run must be reconstructable from persisted events.
- **Testability:** external model calls must have mockable interfaces and recorded fixtures.
- **Safe defaults:** read-only tools, explicit approvals, bounded retries, and abstention when evidence is insufficient.
- **Reproducibility:** synthetic data generation, pinned dependencies, migrations, and documented commands.
- **Honest scope:** NorthForge is a portfolio MVP, not a certified enterprise security or compliance product.

## 8. Suggested checkpoint sequence

Claude Code should stop for review after these milestones:

1. **Foundation:** Phase 0 completed.
2. **Data model:** Phase 1 completed.
3. **First vertical capability:** Phases 2–3 completed.
4. **First AI workflow:** Phases 4–6 completed.
5. **Usable product loop:** Phase 7 completed.
6. **Evidence of improvement:** Phase 8 completed.
7. **Safe public demo:** Phases 9–10 completed.
8. **Portfolio release:** Phase 11 completed.

At each checkpoint, the reviewer should test the actual user flow, not only inspect code. Record feedback in `DECISIONS.md` or a dedicated changelog before the next phase begins.

## 9. Final definition of done

NorthForge is complete for the MVP when a new user can describe a contract/policy-review automation, inspect and edit the proposed workflow, run it over synthetic enterprise documents, see the evidence and execution trace, intervene or retry when necessary, receive a structured result, label a failure, and rerun an evaluation showing whether a subsequent change improved the workflow.

The project is successful when it clearly demonstrates the connection between **product design, agent orchestration, model behavior, workflow state, human supervision, evaluation, reliability engineering, and measurable improvement**.
