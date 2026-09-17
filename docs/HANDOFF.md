# NorthForge handoff (state as of 2026-09-16, end of Phase 4)

Read this first in a new session, then `docs/IMPLEMENTATION_PLAN.md` (phase list), `DECISIONS.md` (ADR-001 to ADR-027), and `LOCAL_RUN.md` (git-ignored local commands). Repo: `C:\Users\shahm\Desktop\Per. Proj\NorthForge\NorthForge`, GitHub `Big-ShahMir/NorthForge`, branch `main`, HEAD is the `docs: record Phase 4 verification` commit on top of `8b4d884`, tree clean, in sync with origin.

## 1. Mission and status

NorthForge is a supervised AI workflow product (portfolio MVP, synthetic data only) for vendor contract and policy review: describe an automation, edit the proposed workflow, run it with read-only tools and citations, supervise, recover, label failures, evaluate across versions. Definition of done: the full journey from `docs/PROJECT_BRIEF.md` works with synthetic data and shows measurable improvement from three failure-driven changes.

| Phase | Scope | Status |
|---|---|---|
| 0 | Bootstrap, health, CI, frontend shell | Done 2026-09-15 |
| 1 | Data model, migrations, Clerk/dev auth, project and workflow CRUD | Done 2026-09-15 |
| 2 | Typed step language, semantic validation, tool registry, catalogs, frontend type generation | Done 2026-09-15 |
| 3 | Synthetic corpus, storage, chunking, full-text retrieval, ingestion, documents/search API | Done 2026-09-16 |
| 4 | NVIDIA provider adapter, capability registry, routing, mock provider, caching, fallback, provider status | Done 2026-09-16 |
| 5 | Planner graph (natural language to validated workflow proposal) | **Next** |
| 6 to 11 | Runner graph, UI, evaluation loop, hardening, deployment, polish | Not started |

## 2. How we work (user-approved process)

- **Fable (lead)** plans each phase in plan mode, writes a design doc (plan file under `C:\Users\shahm\.claude\plans\`), delegates implementation to subagents, reviews the safety-critical code personally, runs every gate, commits, pushes.
- **Subagents:** Sonnet for implementation; Opus for judgment-heavy generation (used for the synthetic dataset). Stage A runs independent agents in parallel with disjoint file ownership; Stage B integrates. Each prompt must include: repo path, files to read, exact interfaces, gates to run, "no commits, no attribution".
- **Commits:** Conventional Commits, direct to `main`, no branches, **never any `Co-Authored-By` or Claude attribution** (contractual; the user had all history rewritten once to remove it). Push after each verified phase. Report hash, message, files, tests, push status at each checkpoint. Stop at phase checkpoints for user review.
- **Never present placeholders as features.** Unbuilt UI areas render a visibly labelled "not yet implemented" page naming the phase.
- Update `DECISIONS.md` (dated ADR) for material choices, `README.md` for setup changes, `docs/API_SPEC.md` and `docs/DATABASE_SPEC.md` when contracts change, `LOCAL_RUN.md` with manual test commands.

## 3. Stack and repository layout

- **Backend** `backend/` (uv project, Python 3.12 pinned, `uv.lock` committed): FastAPI + Pydantic v2 + pydantic-settings, SQLAlchemy 2 async + asyncpg, Alembic (migrations `0001`, `0002`), arq on Redis, PyJWT (Clerk RS256), aiobotocore (S3/MinIO). One package `northforge` shared by API (`python -m northforge.api`) and worker (`python -m northforge.worker`). Modules: `core/` (config, errors, logging, health, queue), `db/` (models, engine, repositories), `schemas/` (workflow language, refs, steps, outputs, evidence, validation, catalog, API models), `auth/`, `api/` (routes: system, projects, workflows, catalog, documents, provider_status), `providers/` (types, errors, base, capabilities + model_catalog.json, nvidia, structured, mock, resilience, cache, router, status, factory, smoke), `tools/` (spec, context, registry, invoke, builtin tools, fixture corpus), `retrieval/` (chunking, retriever protocol, postgres, fixture, rules, embedder + reranker adapters), `storage/` (protocol, s3, memory), `ingestion/` (pipeline, seed CLI), `data/synthetic/` (generator), `worker/`.
- **Frontend** `frontend/`: Vite 7, React 19, TypeScript strict, Tailwind v4, shadcn-style primitives, React Router 7, Vitest. Only the Settings/status page is functional. Types generated from `frontend/openapi.json` into `src/lib/api-schema.d.ts` via `npm run generate:api` (CI fails on drift).
- **Data:** `backend/data/synthetic/` committed (48 docs, manifest with sha256, vendors, 11 policy rules, ground truth, 36 retrieval eval cases). Regenerate: `uv run python -m northforge.data.synthetic --out data/synthetic --seed 20260916 --version v1`.
- **Infra:** `docker-compose.yml` runs Postgres 16 + pgvector (host port **5433**), Redis 7 (6379), MinIO (9000, console 9001). `.env.example` is the template; `.env` (untracked) mirrors it with `AUTH_MODE=dev`.
- **CI** `.github/workflows/ci.yml`: backend job (ruff, mypy, alembic upgrade, pytest with Postgres/Redis/MinIO services, `NORTHFORGE_INTEGRATION=1`, dataset drift check), frontend job, contracts job (OpenAPI export + type generation drift).
- **Tests:** 580 backend (`NORTHFORGE_INTEGRATION=1 uv run pytest -q`, ~85 s, needs the three containers; without the flag DB tests skip when unreachable), 7 frontend. Test DB `northforge_test` is migrated once per session; each test runs in a rolled-back savepoint.

## 4. Key technical decisions (see ADRs for rationale)

- ADR-013/017: Clerk auth (`AUTH_MODE=clerk`, JWKS + issuer + azp checks) with a **dev mode** trusting `X-Dev-User` header, rejected in production. Users provisioned on first request; subject stored as `clerk_user_id` (`dev|<name>` in dev mode).
- ADR-018: other owners' resources return **404**, never 403.
- ADR-019: UUID keys, JSONB, string statuses with CHECK constraints, `alembic check` enforced.
- ADR-021: two validation layers. Parse layer accepts incomplete drafts; semantic layer (`validate_workflow`) runs on validate/approve and returns 422 `INVALID_WORKFLOW` with coded problems and JSON paths. References: `$input.<name>`, `$step.<id>.<field>`; referenced step must be a DAG ancestor.
- ADR-022: every tool call goes through `invoke_tool` (registered → allowlisted → args validated `extra=forbid` → timeout → output validated → trace). Tools: `search_documents`, `get_document_chunk`, `lookup_policy_rules`, all read-only.
- ADR-023: committed deterministic synthetic dataset; generator refuses denylisted real brands.
- ADR-024: retrieval is Postgres full-text (`websearch_to_tsquery`, `ts_rank_cd`), access-group filter in SQL **before** ranking plus Python re-check; outcomes `ok | insufficient_evidence | conflicting_evidence`; AND-match first, OR-fallback at score floor 0.5. `Embedder` protocol exists but unused.
- ADR-025: raw docs in S3/MinIO, chunks in Postgres; storage is a readiness dependency.
- ADR-026: all model calls go through `northforge/providers/` (`ModelProvider` protocol, `NvidiaProvider` over plain `httpx2`, `MockProvider`, `ModelRouter` with capability checks, retries honouring `Retry-After`, per-model circuit breaker, per-provider and per-model semaphores, fallback only on rate limit/outage/timeout, Redis cache for deterministic requests outside production). Missing `NVIDIA_API_KEY` does not stop startup; calls fail `PROVIDER_NOT_CONFIGURED`. `GET /api/provider-status` is authenticated and reads in-memory state only. `ModelRouter.snapshot()` is the JSON for run/version model metadata; `ModelInvocationRecord` is the trace-safe per-call record (Phase 6 persists it).
- ADR-027: role defaults in `providers/model_catalog.json` (planner Nemotron 3 Super, extractor Nemotron 3.5 Lightning, drafter Kimi K3, evaluator DeepSeek V4 Flash, embedding Nemotron 3 Embed 1B at 2048 dims, reranker Llama Nemotron Rerank VL 1B v2). `ProviderEmbedder`/`ProviderReranker` exist but retrieval is still lexical-only; hybrid ranking waits for a failing retrieval eval case.
- Readiness semantics: Postgres/Redis/storage down → 503 `not_ready`; worker heartbeat missing → 200 `degraded`.

## 5. Environment quirks (this machine)

- A native Windows PostgreSQL service occupies port 5432; Compose publishes Postgres on 5433. Never use 5432.
- Docker Desktop must be started manually; MCP Docker server fails until then.
- Memory pressure: only ~1 to 2 GB free with browser/WSL/IDE open; Windows kills background processes. Start the API without the reloader (`uv run uvicorn northforge.api.main:create_app --factory --host 127.0.0.1 --port 8000`) when memory is tight.
- Git Bash: kill processes with `ps -W` + `taskkill //F //PID <winpid>`; the Bash tool truncates very large heredocs (keep file writes under ~150 lines per command).
- Two pytest sessions on the shared test DB deadlock (`migrated_database` downgrades/upgrades). Never run the suite while a subagent runs it.
- Git config email `shahmir.ahmad2302@gmail.com` differs from the initial commit's noreply address; user should ensure both are linked to the GitHub account.
- Files in the working copy are CRLF; git normalises to LF on commit (harmless warnings on `git add`).
- Python `pathlib.read_text()` defaults to cp1252 on this machine: always pass `encoding="utf-8"` in scripts that touch the docs (they contain em dashes).

## 6. Deferred items and follow-ups (tracked)

| # | Item | Why deferred | When to address |
|---|---|---|---|
| D1 | CI status on GitHub. | Resolved: user confirmed the run for `220d6ba` green on 2026-09-16; `gh` still not installed, so later runs are checked by the user. | Ask at the start of each phase. |
| D2 | Clerk not exercised against a live Clerk instance; only local RSA keys in tests. `CLERK_JWKS_URL`, `CLERK_ISSUER`, `VITE_CLERK_PUBLISHABLE_KEY` empty. | User has no Clerk app yet | **Before Phase 7 UI work:** user creates a Clerk application; wire `ClerkProvider` in the frontend and switch `.env` to `AUTH_MODE=clerk` for a manual login test. |
| D3 | Frontend has only the Settings page; sidebar areas are labelled stubs. | Plan puts screens in Phase 7 | Phase 7, or earlier if the user opts to pull forward a thin slice (project list, document browser, search page) after Phase 4. Offer this once Phase 4 is done. |
| D4 | Embeddings/pgvector: adapters (`ProviderEmbedder`, `ProviderReranker`) exist; retrieval still lexical-only. | ADR-027: no evidence of lexical failure yet. | When a retrieval eval case fails hit@8 under honest phrasing: add a 2048-dim `vector` column migration, embed at ingestion, blend scores, re-run `tests/retrieval/test_retrieval_quality.py`. |
| D5 | Model role env vars. | Resolved in Phase 4: six roles configurable, validated at startup, defaults in `model_catalog.json`. | Closed. |
| D6 | Semantic validation `known_tools` is passed from the in-process registry; approve re-validates. Tool `kind` restrictions (`ALLOWED_TOOLS_BY_STEP`) are hard-coded in `schemas/workflow_validation.py`. | Sufficient for three tools | When a fourth tool or a draft-only tool is added (ADR-022 revisit). |
| D7 | `ToolContext.trace` callback exists but nothing persists `ToolCallRecord`s to `trace_events`. Runs/step runs/trace repositories exist with no API. | Runtime is Phase 6 | Phase 6 runner graph: persist tool calls and step outputs as trace events; add run endpoints from `docs/API_SPEC.md`. |
| D8 | Ingestion job status endpoint relies on arq job keys with default expiry; no persistent job table. Ingest endpoint is per-project synthetic-only. | MVP | Phase 6 when run jobs need durable status; consider a `jobs` table then. |
| D9 | Users' access groups are only editable via `python -m northforge.ingestion.seed --grant-user`. No API/UI. | Simulated permissions | Phase 7 settings screen or Phase 9 hardening; document as simulated, not real authorization. |
| D10 | `step_catalog.config_schema` exposes the full step model (common fields included); `compare_policy` marked deterministic though it can use model interpretation. | Editor needs common fields anyway | Phase 7 editor: revisit if the editor needs config-only schemas. |
| D11 | Malformed synthetic document (`doc_saltmarsh_msa`) ingests with 2 warnings; `\x00` avoided by design. Long document has 47 sections. | Intentional fixtures | Phase 8 evaluation cases should reference these. |
| D12 | Windows worker cannot install POSIX signal handlers; in-flight jobs are not drained on stop. | Platform | Phase 10 deployment (Linux) makes this moot; note in ops docs. |
| D13 | `docs/ARCHITECTURE.md` and `docs/TECH_STACK.md` not yet updated for MinIO-in-Phase-3 or lexical-first retrieval beyond the ADRs. | Minor | Phase 11 doc sweep, or when touching those files. |
| D14 | `npm audit` shows 2 moderate advisories in transitive frontend deps. | Not blocking | Phase 9 hardening. |
| D16 | Planner primary `nvidia/nemotron-3-super-120b-a12b` is intermittent on the hosted endpoint (tool calls HTTP 500 in 2 of 3 probes, one 60 s timeout); the router falls back to Lightning. | User's model choice; fallback covers it | Phase 5: count fallbacks in planner fixtures; if high, set `NVIDIA_MODEL_PLANNER=nvidia/nemotron-3.5-lightning-30b-a3b`. |
| D17 | Kimi K3 (drafter) and DeepSeek V4 Flash (evaluator) take ~2 min per request on the free tier (queueing). | Hosted quota | Phase 8 evaluation: keep runs small, rely on the cache, or move those roles to Nemotron models via env vars. |
| D18 | The commit `8b4d884` mixes the Phase 4 doc updates with the thinking-off fix (docs were staged before the fix landed). | History is not rewritten | None; noted for reviewers. |
| D15 | Frontend `Envelope<T>` is hand-written (openapi-typescript emits concrete `Envelope_X_` types). | Cosmetic | Leave unless it drifts. |

## 7. Phase 5 plan seed (next steps, exact)

Goal per `docs/IMPLEMENTATION_PLAN.md` Phase 5: a LangGraph planner graph that turns a natural-language automation request into an editable, validated workflow proposal. Store the original request, planner model snapshot, proposed workflow, and validation warnings; reject unsupported tools and unsafe or ambiguous actions; ask for clarification or fall back safely when underspecified; fixtures for common, ambiguous, and malicious requests.

What Phase 4 hands the planner:
- `ModelRouter` on `app.state.model_router` (dependency `get_model_router`) and `ctx["model_router"]` in the worker. Use `router.generate_structured("planner", request, schema)` with `temperature=0` (cached in dev) and `router.tool_call("planner", ...)` for tool selection; `tool_definition_from_spec` turns registry `ToolSpec`s into tool definitions.
- `router.snapshot()` is the JSON to store in `workflow_versions.model_config_json`; pass a `trace` callback collecting `ModelInvocationRecord`s for later persistence.
- Planner prompts must follow the prompt-boundary rules in `docs/MODEL_ROUTING.md` (system policy, user request, retrieved text labelled untrusted). The verified planner model (`nvidia/nemotron-3-super-120b-a12b`) accepts `json_schema` structured output and tool calls with thinking off (the catalog default); `nvext_guided_json` is not needed.

Design points to settle in plan mode:
1. Add `langgraph` (check the lock for a compatible version with pydantic 2.x) or a hand-rolled state machine; the plan requires LangGraph for planner and runner.
2. Output schema for the proposal: reuse `schemas/workflow.py` definitions plus `assumptions`, `clarifying_questions`, `rejected_actions`; run `validate_workflow` on the proposal and feed problems back for one repair turn.
3. API: `POST /api/projects/{id}/workflows/plan` (202 + job id via arq) per `docs/API_SPEC.md`; persist the draft version with `source_request` and `model_config_json`.
4. Fixtures: planner tests use `MODEL_PROVIDER=mock` with scripted proposals (valid, invalid-then-repaired, unsupported tool, prompt-injection request); one live test gated on `NORTHFORGE_LIVE_MODELS=1`.
5. Delegation: Sonnet for graph + schema + fixtures, Sonnet for API route + job + tests; Fable reviews prompt boundaries and the rejection logic.

Before starting Phase 5: `docker compose up -d --wait`, confirm `git status` clean, ask the user for the CI result of the Phase 4 push (D1).

## 8. Quick commands

```bash
docker compose up -d --wait                      # postgres 5433, redis 6379, minio 9000/9001
cd backend && uv sync && uv run alembic upgrade head
uv run ruff format --check . && uv run ruff check . && uv run mypy
NORTHFORGE_INTEGRATION=1 uv run pytest -q         # 401 tests
uv run python -m northforge.api                   # or the uvicorn --factory form when memory is tight
uv run python -m northforge.worker                # --check for liveness; python -m northforge.worker.ping for a round trip
uv run python -m northforge.ingestion.seed --project-id <uuid> [--grant-user "dev|alice" legal_restricted]
uv run python -m northforge.providers.smoke [--verbose] [--json]   # live per-role verification, ~6 min (Kimi/DeepSeek queue ~2 min each)
curl -s http://127.0.0.1:8000/api/provider-status -H "X-Dev-User: alice"
uv run python -m northforge.api.export_openapi && cd ../frontend && npm run generate:api
cd frontend && npm install && npm run dev         # http://localhost:5173/settings
```

Dev user `alice` (header `X-Dev-User: alice`) already owns four projects in the local dev database, one seeded with the corpus.
