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

## ADR-017: Authentication modes and just-in-time user provisioning

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** The API supports two authentication modes selected by `AUTH_MODE`. `clerk` verifies Clerk session JWTs (RS256) against the Clerk JWKS endpoint with issuer and optional authorized-party checks. `dev` trusts an `X-Dev-User` header and is rejected at startup when `APP_ENV=production`. Users are provisioned on first authenticated request by upserting on the Clerk subject identifier.  
**Context:** Clerk is the chosen identity provider (ADR-013), but local development, tests, and CI must not require a Clerk account. Token verification is deterministic code that needs no Clerk SDK.  
**Alternatives:** Clerk Backend SDK; a shared static API key for development; no development mode.  
**Consequences:** Every project-data route depends on `get_current_user`. Tests inject a `Principal` directly. The dev header is documented as a local convenience, never a security boundary.  
**Revisit when:** Organisations or memberships are introduced, or Clerk changes its token format.

## ADR-018: Unauthorized access to another owner's resources returns 404

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Repository reads are scoped by owner. A resource that exists but belongs to another user is indistinguishable from a missing one: the API returns `NOT_FOUND`. `FORBIDDEN` is reserved for authenticated actions a user is not allowed to perform on resources they can see.  
**Context:** Returning 403 for other users' identifiers confirms that the identifier exists, which leaks information across tenants.  
**Consequences:** Cross-owner tests assert 404. Authorization lives in the repository layer, so no route can forget it.  
**Revisit when:** Shared projects or memberships require visible-but-restricted resources.

## ADR-019: Database conventions

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** UUID primary keys, timezone-aware timestamps, JSONB for versioned documents and payloads, string status columns with CHECK constraints instead of native Postgres enums, SQLAlchemy 2 async with asyncpg, and hand-written Alembic migrations verified against the models with `alembic check`.  
**Context:** Native enums make additive status changes require type migrations. CHECK constraints keep the allowed values visible in the schema while remaining cheap to change. `alembic check` guards against model and migration drift in CI.  
**Consequences:** Adding a status value is a one-line constraint change plus a migration. Workflow definitions are validated by Pydantic on every read and write, not by the database.  
**Revisit when:** Query patterns need typed columns extracted from JSON documents.

## ADR-020: Workflow definition envelope defined in Phase 1, step language in Phase 2

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Phase 1 fixes the top-level workflow document shape (`schema_version`, name, request, inputs, steps, edges, tools, output schema, approval points, policies), the eight step types, and structural validators (unique ids, edges reference steps, acyclic, one finish step). Per-step input, output, timeout, retry, and failure fields are permitted but not yet typed; Phase 2 tightens them.  
**Context:** Persistence and versioning need a stable, validated document now. Typing every step before tools exist would be speculative.  
**Consequences:** Definitions saved in Phase 1 remain valid after Phase 2 as long as they only use the envelope fields. Phase 2 adds a `schema_version` bump only if the envelope changes.  
**Revisit when:** Phase 2 begins.

## ADR-021: Two workflow validation layers and a string reference syntax

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Workflow definitions are checked in two layers. The parse layer (Pydantic, on every read and write) enforces shape, enums, unique ids, referential edges, an acyclic graph, and one finish step; every per-type field has a default so a document is never rejected for being incomplete. The semantic layer (`validate_workflow`) runs on the validate and approve endpoints and reports coded errors (missing required configuration, dangling or non-ancestor references, undeclared or unregistered tools, approval point mismatches) and warnings. Step inputs reference data with the strings `$input.<name>` and `$step.<step_id>.<field>`.  
**Context:** The UI separates Save Draft from Validate and Approve. Users must be able to save partial work, but nothing incomplete may become approved or executable. A string reference form keeps definitions diff-friendly and readable in JSON.  
**Alternatives:** One strict schema that rejects incomplete drafts; structured reference objects; JSONPath expressions.  
**Consequences:** `validation_warnings_json` keeps storing warnings; semantic errors return 422 `INVALID_WORKFLOW` with per-problem codes and paths and leave the version in draft. The planner (Phase 5) and editor (Phase 7) target the same problem codes.  
**Revisit when:** Steps need to consume nested output fields or computed expressions.

## ADR-022: Tool registry with fixture-backed read-only tools

**Date:** 2026-09-15  
**Status:** Accepted  
**Decision:** Tools are registered with a typed specification (name, description, input and output models, side-effect class, access scope, kind) and are invoked only through `invoke_tool`, which checks registration, the workflow version's declared tool allowlist, argument validity, a timeout, and output validity before returning, classifying every failure with the evaluation taxonomy. Phase 2 ships three read-only tools (`search_documents`, `get_document_chunk`, `lookup_policy_rules`) backed by a small deterministic synthetic corpus with documented failure triggers and access groups.  
**Context:** The runtime, planner, and evaluator all need typed tool contracts and deterministic doubles before real retrieval exists. Centralising the checks in the invoker means no tool implementation can bypass them.  
**Alternatives:** LangChain tool abstractions directly; ad-hoc function calls inside graph nodes.  
**Consequences:** Phase 3 replaces the search implementation with real retrieval over the generated corpus while keeping the same contracts and tests. Failure triggers (`__timeout__`, `__error__`, `doc_malformed`, restricted chunks) stay available for runtime and evaluation tests.  
**Revisit when:** A draft-only tool (for example creating a review summary artifact) is added, or tools need per-project configuration.

## ADR-023: Committed, deterministically generated synthetic dataset

**Date:** 2026-09-16  
**Status:** Accepted  
**Decision:** A seeded generator under `northforge.data.synthetic` produces the contract, policy, and vendor corpus, its ground truth, policy rules, and retrieval evaluation cases as JSON. The generated output under `backend/data/synthetic/` is committed, and CI regenerates it and fails on any drift.  
**Context:** Reviewers and evaluation reports need to read the exact documents a run used. Committing the output makes citations reproducible across machines while the generator remains the source of truth. All content is fictional; the generator carries a denylist of real company names that tests enforce.  
**Alternatives:** Generate at test time only; hand-write documents; use public contract datasets (licensing and realism risks).  
**Consequences:** Changing a template changes hashes and therefore the dataset version. Edge cases (missing clauses, conflicting policies, ambiguous terms, long and malformed documents, restricted access groups, near duplicates, prompt-injection fixtures) are part of the corpus rather than separate test doubles.  
**Revisit when:** The corpus needs a second vertical or externally sourced documents.

## ADR-024: Full-text retrieval first, embeddings deferred, explicit abstention outcomes

**Date:** 2026-09-16  
**Status:** Accepted  
**Decision:** Retrieval ranks chunks with PostgreSQL full-text search (`websearch_to_tsquery` and `ts_rank_cd`) behind a `Retriever` interface. Access groups, project, document type, and vendor are filtered in SQL before ranking, and every returned chunk is re-checked in Python. A retrieval returns an outcome of `ok`, `insufficient_evidence` (no results or a top score below the threshold), or `conflicting_evidence` (two policy documents for the same policy area with different effective dates and no supersession link). An `Embedder` protocol exists but is not used until Phase 4 selects a hosted or local embedding model.  
**Context:** Lexical search is deterministic, dependency-free, and testable with exact recall thresholds. Adding a local embedding model would add a multi-gigabyte dependency to a memory-constrained development machine before the model provider layer exists.  
**Alternatives:** pgvector with local sentence-transformers now; hosted embeddings now; hybrid search now.  
**Consequences:** Semantic recall is limited to term overlap in Phase 3, which the evaluation fixtures are written to respect. Phase 4 can add a hybrid ranker without changing tool contracts.  
**Revisit when:** Phase 4 chooses an embedding model, or retrieval evaluation shows recall failures that lexical search cannot fix.

## ADR-025: S3-compatible object storage for raw documents, chunks in PostgreSQL

**Date:** 2026-09-16  
**Status:** Accepted (supersedes the deferral in ADR-015)  
**Decision:** Ingestion writes each raw document to S3-compatible object storage (MinIO locally, a managed bucket in deployment) and stores chunks with offsets, headings, and a generated `tsvector` in PostgreSQL. Storage is a readiness dependency.  
**Context:** The technology stack requires documents and artifacts outside the database, while search needs the text in PostgreSQL. Keeping both keeps citations reconstructible from storage and search fast.  
**Alternatives:** Text only in PostgreSQL; only in object storage with an external index.  
**Consequences:** One more local container and four required environment variables. Tests use an in-memory storage double; the S3 client is exercised by integration tests only.  
**Revisit when:** Document volume or file types (PDFs, scans) require a separate processing pipeline.

## ADR-026: Provider-agnostic model layer with capability-checked routing, retries, circuit breaking, and fallback

**Date:** 2026-09-16  
**Status:** Accepted  
**Decision:** All model calls go through `northforge.providers`: a `ModelProvider` protocol (`generate`, `generate_structured`, `tool_call`, `embed`, `rerank`, `count_tokens`) with normalised request/response types, an `NvidiaProvider` over the hosted OpenAI-compatible endpoint (plain `httpx2`, no vendor SDK), a scripted `MockProvider`, and a `ModelRouter` that resolves each of six roles (planner, extractor, drafter, evaluator, embedding, reranker) to a primary model and fallbacks. Model ids and capabilities live in `model_catalog.json` (overridable by `MODEL_CAPABILITIES_FILE`), routes are overridable by environment variables, and startup fails with every routing problem listed when a model is missing from the catalog or lacks the capabilities its role needs. The router applies per-model and per-provider concurrency limits, exponential backoff that honours `Retry-After`, a per-model circuit breaker, and falls back to the next model only for rate limits, outages, timeouts, and open circuits; malformed output (after one repair request) and rejected requests fail immediately. Every failure is a `ProviderError` subclass with a stable `PROVIDER_*` code and a user-safe message. Deterministic requests (temperature 0, or explicitly flagged) are cached in Redis outside production. Without `NVIDIA_API_KEY` the application still starts; calls fail with `PROVIDER_NOT_CONFIGURED`, and `GET /api/provider-status` reports in-memory router state without touching the provider. Every routed call can emit a `ModelInvocationRecord` (provider, model, outcome, latency, attempts, cache hit, fallback, usage; never prompt text or keys) for trace persistence in Phase 6, and `ModelRouter.snapshot()` gives the JSON stored as run and version model metadata.  
**Context:** The hosted NVIDIA free tier is 40 requests per minute per key across all models and returns 429 under load; the planner, runner, and evaluator must degrade predictably and stay testable offline. `docs/MODEL_ROUTING.md` requires configuration-driven model choice and a mock provider.  
**Alternatives:** OpenAI SDK or LangChain NVIDIA integration (extra dependency, less control over error mapping and secrets); one model for all roles; retries without circuit breaking.  
**Consequences:** One new runtime dependency (`httpx2`), a JSON catalog to keep accurate, and per-role structured-output modes (`json_schema`, `nvext_guided_json`, `prompt_only`) that a live smoke command verifies and records. Readiness is deliberately independent of provider health.  
**Revisit when:** A second generation provider is added, or evaluation shows a role needs a different fallback policy.

## ADR-027: Model assignment per role; embedding and reranking adapters now, hybrid ranking deferred on evidence

**Date:** 2026-09-16  
**Status:** Accepted  
**Decision:** Default routes: planner `nvidia/nemotron-3-super-120b-a12b` (fallback `nvidia/nemotron-3.5-lightning-30b-a3b`), extractor `nvidia/nemotron-3.5-lightning-30b-a3b` (fallback the Super model), drafter `moonshotai/kimi-k3` (fallback the Super model), evaluator `deepseek-ai/deepseek-v4-flash-0731` (fallback the Lightning model), embedding `nvidia/nemotron-3-embed-1b` (2048 dimensions), reranker `nvidia/llama-nemotron-rerank-vl-1b-v2`. The evaluator is deliberately a different model family from the planner and extractor. Phase 4 ships the embedding and reranking adapters (`ProviderEmbedder`, `ProviderReranker`) behind the existing `Embedder` protocol and a new `Reranker` protocol, with mock implementations, but does not add a pgvector column, embed chunks at ingestion, or change `PostgresRetriever`'s ranking.  
**Context:** The retrieval quality gate (`tests/retrieval/test_retrieval_quality.py`) passes with lexical ranking on the synthetic corpus, and ADR-024 committed to adding semantic ranking only on evidence. The user chose "adapters only" for this phase.  
**Alternatives:** Hybrid lexical plus vector ranking now; reranking the top candidates now.  
**Consequences:** Hybrid ranking becomes a small, contained change later. The trigger for doing it: a retrieval evaluation case that lexical search cannot satisfy (a `normal` case failing hit@8 after honest query phrasing, or a paraphrase-heavy category added in Phase 8), measured before and after with the same gate.  
**Revisit when:** That trigger fires, or the drafter needs passages beyond what lexical search returns for citation.

## ADR-028: LangGraph planner with a code-owned enforcer, one repair turn, and bounded tool probes

**Date:** 2026-09-18  
**Status:** Accepted  
**Decision:** The Phase 5 planner is a LangGraph `StateGraph` (`northforge/planner/graph.py`) whose nodes call `ModelRouter` directly: screen, ground, probe tools, propose, compile, one conditional repair, finalize. The model produces a deliberately flat `PlannerProposal` (steps as `{id, type, label, ..., config}`) rather than the discriminated `WorkflowDefinition`, and code (`compile.py`) turns it into a definition under fixed rules the model cannot override: only registered tools survive (dropped ones become `rejected_actions`), `policies` is always empty, `approval_points` are always recomputed, a `human_review` step is inserted before `finish` when missing (and recorded as an assumption), and the result passes the same `parse_definition` plus `validate_workflow` the validate endpoint uses. Problems are fed back once as a repair turn; remaining errors are persisted with the draft (ADR-021 allows semantically invalid drafts) so the user can edit rather than lose the proposal. A deterministic pre-model screen (`screen.py`) records side-effect requests and prompt-injection markers as rejections; prompts label every untrusted block and break `</` sequences so user text cannot close its delimiter. Before proposing, the planner may run at most two `tool_call` rounds (four calls) through `invoke_tool` with the caller's real, access-filtered `ToolContext`; results are embedded as untrusted `<tool_result>` blocks in the propose prompt rather than replayed as tool-role history. Planning is asynchronous: `POST /api/projects/{id}/workflows/plan` and `POST /api/workflows/{id}/plan` (re-plan with answers) enqueue an arq job that persists a draft version with `source_request`, `model_config_json = router.snapshot()`, `validation_warnings_json`, and a new `planner_output_json` column (assumptions, clarifying questions, rejected actions, remaining problems, trace-safe model and tool records, fallback count). A rejected request persists nothing; the job result carries the reasons.  
**Context:** `docs/IMPLEMENTATION_PLAN.md` requires LangGraph for planner and runner; the hosted planner model accepts `json_schema` output but the full step union is too large and brittle for guided decoding; safety properties (no side effects, no unregistered tools, supervision before finish) must not depend on model compliance; `docs/API_SPEC.md` requires long-running operations to return a job identifier.  
**Alternatives:** A hand-rolled state machine until Phase 6 (two idioms); letting the model emit `WorkflowDefinition` directly; a synchronous endpoint; no grounding, or grounding from catalog facts only; hard-stopping on injection markers instead of recording and continuing with the legitimate part of the request.  
**Consequences:** `langgraph` adds 24 transitive packages (langchain-core, langsmith, httpx 0.28 beside httpx2, orjson, tenacity) and pins websockets to 16.x. A plan costs two to five model requests. `planner_output_json` describes the proposal as generated and is not updated when the user edits the draft. Phase 6 reuses the graph idiom and langgraph's checkpointer for the runner.  
**Revisit when:** The runner needs a different persistence shape for graph state, a draft-only tool is added (the enforcer's tool rule must then consult side-effect class, not just registration), or planner evaluation shows the single repair turn is insufficient.

## Decision template

### ADR-XXX: Title

**Date:** YYYY-MM-DD  
**Status:** Proposed / Accepted / Superseded  
**Decision:**  
**Context:**  
**Alternatives:**  
**Consequences:**  
**Revisit when:**  
