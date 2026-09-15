# NorthForge Architecture

## Architectural goal

NorthForge must make the relationship between user intent, workflow definition, model behavior, runtime state, human intervention, and evaluation visible. The system should be modular enough to replace models or hosting without rewriting product behavior.

## High-level topology

```text
Browser
  -> React web app
  -> FastAPI API
       -> PostgreSQL: users, projects, workflows, runs, traces, evals
       -> Redis-backed queue
       -> NVIDIA model adapter
       -> Object storage
  -> Worker
       -> LangGraph planner, runner, and evaluator
       -> PostgreSQL checkpoints and trace events
       -> NVIDIA model adapter
```

## Components

### Web application

Owns navigation, workflow editing, run supervision, trace rendering, approvals, feedback, and evaluation views. It communicates with the API using typed contracts and does not call the model provider directly.

### API service

Owns authentication, authorization, project and workflow CRUD, run creation/control, trace queries, feedback, evaluation requests, and provider status. It should remain stateless and enqueue long-running work.

### Worker

Consumes jobs for workflow execution, ingestion, replay, and evaluations. It invokes LangGraph graphs, persists checkpoints and trace events, and updates run status. Worker restarts must not lose durable state.

### Runtime

Contains three graph families: planner graph for natural language to validated workflow, runner graph for approved workflow execution, and evaluator graph for replay and grading. Deterministic nodes handle validation, access filtering, policy checks, formatting, and persistence.

### Model gateway

Provides one internal interface for generation, structured output, tool selection, embeddings, and reranking. Provider-specific SDK details stay behind the gateway.

### Retrieval

Ingests synthetic documents, chunks them, adds metadata and access groups, retrieves relevant passages, and returns citation-ready source references. Retrieval is filtered by permission before model context construction.

## Data flow

A request enters the API, is stored, and becomes a planner job. The planner produces a workflow JSON document, which is validated and returned to the UI. On approval, the API creates a run and enqueues it. The worker executes graph nodes, persisting state and trace events after meaningful boundaries. The UI polls or streams run updates. Completion stores the structured result. Feedback may create an evaluation case. Evaluation jobs replay cases against a workflow version and store metrics and raw failures.

## State model

Workflow state is versioned and immutable after approval. Runs reference a specific workflow version. Checkpoints contain the graph state needed to resume. Trace events are append-oriented. Feedback and evaluation records reference the run and workflow version that produced them.

## Reliability strategy

Use bounded retries, categorized errors, timeouts, idempotency keys, checkpointing, cancellation, and explicit terminal states. Keep MVP tools read-only or draft-only so retrying cannot create duplicate real-world effects. A run must never be marked successful solely because the model returned text.

## Technology choices

React/TypeScript, FastAPI/Pydantic, LangGraph, selective LangChain integrations, NVIDIA OpenAI-compatible API, PostgreSQL, optional pgvector, Redis-backed queue, S3-compatible storage, OpenTelemetry-compatible events, Docker Compose locally, and Render for deployment.

## Scaling path

The MVP uses one API, one worker class, Postgres, and one queue. Later, high-throughput workflows can split workers by job type, add a dedicated vector store, adopt Temporal for stronger workflow durability, or introduce event streaming. These are not MVP requirements.

## Architecture constraints

No direct model calls from the browser. No secrets in source control. No arbitrary tool execution. No unversioned workflow behavior. No use of real sensitive data in the demo. Any architecture change must be documented in `DECISIONS.md`.
