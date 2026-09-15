# NorthForge Technology Stack

## Required stack

| Area | Choice | Rule |
|---|---|---|
| Frontend | React + TypeScript | Typed UI state and API contracts. |
| Workflow UI | React Flow or equivalent | Use an editable step graph only where it improves clarity. |
| Styling | Tailwind CSS + shadcn/ui | Use consistent design tokens and accessible primitives. |
| API | Python + FastAPI | Keep request handling stateless and typed. |
| Schemas | Pydantic | Validate workflows, tools, model outputs, traces, and evaluation records. |
| Orchestration | LangGraph | Explicit stateful graphs with checkpoints and human-in-the-loop control. |
| Integrations | LangChain selectively | Use for adapters and retrieval helpers, not opaque business logic. |
| Models | NVIDIA API Catalog/NIM | Call through a provider abstraction and backend-only credentials. |
| Database | PostgreSQL | Source of truth for durable product and runtime metadata. |
| Vector search | pgvector where practical | Isolate behind a retrieval interface so it can be replaced. |
| Queue | Redis-backed queue | Separate API request path from long-running work. |
| Worker | Python worker | Runs graphs, ingestion, replay, and evaluation jobs. |
| Storage | S3-compatible object storage | Store documents and artifacts outside the database. |
| Observability | Structured JSON events and metrics | Persist trace events needed for run reconstruction. |
| Testing | pytest, Vitest, Playwright as appropriate | Test deterministic code heavily and model calls through mocks. |
| Local environment | Docker Compose | Provide Postgres and queue dependencies reproducibly. |
| Deployment | Render | Web service, worker, managed Postgres, and queue. |

## Dependency rules

Prefer standard-library or existing-stack solutions. Add a dependency only if it removes substantial complexity or enables a visible requirement. Pin or lock versions. Keep provider-specific packages behind adapters. Do not add a framework only because it is popular.

## Model-provider rules

NVIDIA is the default provider, but NorthForge must not embed one model name in business logic. Store provider, model, capability flags, timeout, and retry policy in configuration. Provide a mock provider for tests and development without credentials.

## Environment variables

At minimum: `DATABASE_URL`, `REDIS_URL`, `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODEL_PLANNER`, `NVIDIA_MODEL_EXTRACTION`, `NVIDIA_MODEL_DRAFTER`, `NVIDIA_MODEL_EVALUATOR`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `RERANKER_PROVIDER`, `RERANKER_MODEL`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and application session settings. Use .env.example; never commit actual values. Embeddings and reranking may use local models, so their provider variables must remain separate from NVIDIA generation-model variables.

## Coding standards

Use typed Python, small modules, explicit error types, async I/O where beneficial, formatters and linters, schema-first boundaries, and tests near the affected module. Frontend components should handle loading, empty, success, partial, and error states.

## Deliberate exclusions

Do not use Kubernetes, a service mesh, a custom event bus, GPU hosting, arbitrary code execution, or a general-purpose multi-agent framework in the MVP.
