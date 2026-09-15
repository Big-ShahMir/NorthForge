# NorthForge Architecture Decisions

This file records material choices, alternatives, assumptions, and reversibility. Add a dated entry when changing architecture or MVP scope.

## ADR-001: Focus on contract and policy review

**Status:** Accepted.

**Decision:** Use synthetic vendor contracts and company policies for the initial vertical.

**Reason:** The workflow naturally demonstrates retrieval, extraction, comparison, citations, uncertainty, human review, and evaluation without requiring real customer data.

**Trade-off:** It may not represent every enterprise workflow. The project must avoid legal-advice or compliance claims.

## ADR-002: Use LangGraph for orchestration

**Status:** Accepted.

**Decision:** Use LangGraph for planner, runner, and evaluator graphs, with LangChain integrations selectively.

**Reason:** NorthForge needs explicit state, checkpoints, human pauses, deterministic nodes, and inspectable transitions.

**Alternatives:** Microsoft Agent Framework, OpenAI Agents SDK, Temporal, CrewAI, and AutoGen. Reconsider if the target role or deployment requirements change materially.

## ADR-003: Use NVIDIA API as default model provider

**Status:** Accepted.

**Decision:** Use NVIDIA’s OpenAI-compatible hosted endpoints through a provider adapter.

**Reason:** The user requested NVIDIA NIM/API models and the interface supports provider abstraction.

**Risk:** Model availability, account verification, quotas, and free-tier limits may change. Mocks and configurable routing are mandatory.

## ADR-004: Keep the runtime provider-agnostic

**Status:** Accepted.

**Decision:** Workflow code calls a `ModelProvider` interface rather than an NVIDIA SDK directly.

**Reason:** It enables testing, fallback, model comparison, and future local or hosted providers.

## ADR-005: Use a separate worker

**Status:** Accepted.

**Decision:** Long-running runs, ingestion, replay, and evaluation execute in a queue-backed worker.

**Reason:** Model calls and human pauses must not block API requests. Worker restarts should resume from durable checkpoints.

## ADR-006: Read-only and draft-only tools in MVP

**Status:** Accepted.

**Decision:** Do not execute real external side effects.

**Reason:** It simplifies safety, retry semantics, testing, and open-source distribution while preserving the supervision signal.

## ADR-007: Synthetic data only

**Status:** Accepted.

**Decision:** Use generated or clearly reusable data in the public project.

**Reason:** The project should be safe to publish and reproduce.

## ADR-008: Render for initial hosting

**Status:** Accepted.

**Decision:** Use Render web service, worker, managed Postgres, and queue for the demo.

**Reason:** It is sufficient for a portfolio deployment without Kubernetes complexity. Reconsider for sustained workload or GPU hosting.

## ADR-009: Commit specifications and use canonical document names

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Track `docs/` and `.claude/CLAUDE.md` in Git. Specifications use the canonical file names referenced by the instructions (`docs/PRODUCT_SPEC.md`, `docs/WORKFLOW_SPEC.md`, and so on). Architecture decisions live in `DECISIONS.md` at the repository root.  
**Context:** The initial `.gitignore` excluded `docs/` and `.claude/`, which contradicted the goal of a reproducible open-source repository and the instructions to update `DECISIONS.md` and `README.md`.  
**Alternatives:** Keep the specifications private and outside the repository.  
**Consequences:** Specifications are versioned with the code and reviewable in pull requests.  
**Revisit when:** The project is opened to external contributors who need a different documentation layout.

## ADR-010: Single shared Python package with separate API and worker entry points

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** One Python package, `northforge`, under `backend/` contains schemas, database, runtime, and model-provider code. The API (`northforge.api`) and worker (`northforge.worker`) are separate entry points that import the same package.  
**Context:** The API and worker must share workflow schemas, persistence, and provider adapters. Separate packages would duplicate logic and drift.  
**Alternatives:** Separate `api/` and `worker/` projects with a third shared package; a monorepo of independent services.  
**Consequences:** One lockfile, one test suite, one type-check run. The services deploy from the same image or source tree with different commands.  
**Revisit when:** The API and worker require independent deployment, scaling, or ownership.

## ADR-011: Python 3.12 with uv, Node 22 with npm

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Pin Python to 3.12 and manage the backend with `uv` and a committed `uv.lock`. Use Node 22 and `npm` with a committed `package-lock.json` for the frontend.  
**Context:** The development machine has Python 3.9 through 3.12 and `uv` installed; Python 3.10 reaches end of life in October 2026. No Poetry, pnpm, or yarn is installed, and adding them would be an unnecessary dependency.  
**Alternatives:** Poetry or pip-tools; pnpm.  
**Consequences:** Reproducible installs with lockfiles and no extra tooling to install.  
**Revisit when:** A required dependency drops support for the pinned versions.

## ADR-012: arq as the Redis-backed job queue

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Use `arq` for the Redis-backed queue and worker process.  
**Context:** The API is asyncio-based FastAPI. Durable run state lives in PostgreSQL checkpoints and trace events, so the queue only needs at-least-once delivery with retries, timeouts, and job identity.  
**Alternatives:** Celery (heavier, sync-first), RQ (sync), Dramatiq, a PostgreSQL-only queue (would remove Redis but contradicts the technology stack).  
**Consequences:** Small dependency footprint and native async jobs. Job idempotency must be enforced in application code using run identifiers.  
**Revisit when:** Workflow durability needs exceed what checkpoints plus a simple queue provide; Temporal is the documented scaling path.

## ADR-013: Clerk for authentication

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Use Clerk for user authentication. The frontend uses Clerk React components and hooks. The API verifies Clerk session tokens server-side and maps the Clerk user identifier to the `users` table.  
**Context:** The database specification defers to "the existing authentication integration," but none existed. Building password management in-house adds security surface to a portfolio MVP.  
**Alternatives:** Built-in email and password sessions; Auth0; magic-link only.  
**Consequences:** Clerk credentials (`CLERK_SECRET_KEY`, `VITE_CLERK_PUBLISHABLE_KEY`) become required configuration for authenticated routes. Tests use a fake token verifier so no Clerk account is needed to run the test suite. Authentication integration is implemented in Phase 1; Phase 0 only reserves configuration.  
**Revisit when:** Multi-tenant membership or self-hosting requirements make an external identity provider unsuitable.

## ADR-014: Vite for the frontend build

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Use Vite with React, TypeScript, React Router, Tailwind CSS, shadcn/ui, and Vitest. The production build is static and served by the FastAPI web service on Render.  
**Context:** The deployment specification describes one web service serving FastAPI and the compiled frontend. A framework with its own server (Next.js) would add a second runtime.  
**Alternatives:** Next.js; Create React App (deprecated).  
**Consequences:** Local development uses the Vite dev server proxying `/api` to FastAPI.  
**Revisit when:** Server-side rendering or edge rendering becomes a requirement.

## ADR-015: Defer local object storage to Phase 3

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Reserve the S3 configuration variables in `.env.example` now, mark them optional, and add MinIO to Docker Compose when document ingestion begins in Phase 3.  
**Context:** Nothing before Phase 3 reads or writes object storage. Adding a service that no code uses would violate the rule that every dependency supports a visible capability.  
**Alternatives:** Add MinIO immediately.  
**Consequences:** Readiness checks cover PostgreSQL, Redis, and the worker until Phase 3.  
**Revisit when:** Phase 3 begins.

## ADR-016: Specification precedence for step types and failure taxonomy

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Where `IMPLEMENTATION_PLAN.md` lists fewer step types or failure categories than the detailed specifications, the detailed specification wins: `WORKFLOW_SPEC.md` defines the eight step types (including `validate_output`) and `EVALUATION_SPEC.md` defines the eleven failure categories.  
**Context:** The implementation plan summarises both lists and omits entries.  
**Consequences:** Schemas in Phase 2 and Phase 8 follow the detailed specifications.  
**Revisit when:** A specification is amended.

## Decision template

### ADR-XXX: Title

**Date:** YYYY-MM-DD  
**Status:** Proposed / Accepted / Superseded  
**Decision:**  
**Context:**  
**Alternatives:**  
**Consequences:**  
**Revisit when:**  
