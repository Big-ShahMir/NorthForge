# NorthForge handoff (state as of 2026-09-18, end of Phase 5)

Read this first in a new session, then `docs/IMPLEMENTATION_PLAN.md` (phase list), `DECISIONS.md` (ADR-001 to ADR-028), and `LOCAL_RUN.md` (git-ignored local commands). Repo: `C:\Users\shahm\Desktop\Per. Proj\NorthForge\NorthForge`, GitHub `Big-ShahMir/NorthForge`, branch `main`, HEAD is the `docs: record planner design` commit, tree clean, in sync with origin.

## 1. Mission and status

NorthForge is a supervised AI workflow product (portfolio MVP, synthetic data only) for vendor contract and policy review: describe an automation, edit the proposed workflow, run it with read-only tools and citations, supervise, recover, label failures, evaluate across versions. Definition of done: the full journey from `docs/PROJECT_BRIEF.md` works with synthetic data and shows measurable improvement from three failure-driven changes.

| Phase | Scope | Status |
|---|---|---|
| 0 | Bootstrap, health, CI, frontend shell | Done 2026-09-15 |
| 1 | Data model, migrations, Clerk/dev auth, project and workflow CRUD | Done 2026-09-15 |
| 2 | Typed step language, semantic validation, tool registry, catalogs, frontend type generation | Done 2026-09-15 |
| 3 | Synthetic corpus, storage, chunking, full-text retrieval, ingestion, documents/search API | Done 2026-09-16 |
| 4 | NVIDIA provider adapter, capability registry, routing, mock provider, caching, fallback, provider status | Done 2026-09-16 |
| 5 | Planner graph (natural language to validated workflow proposal), async planning endpoints | Done 2026-09-18 |
| 6 | Runner graph (execute approved workflows, checkpoints, approvals, trace) | **Next** |
| 7 to 11 | UI, evaluation loop, hardening, deployment, polish | Not started |

Phase 5 commits: `40c872e` (langgraph + contracts), `2db8343` (persistence, migration 0003), `758b020` (planner graph), `5d70d82` (endpoints + worker job), `fa5d648` (live test), then two fixes found in the end-to-end check (worker circular import; planner reference and ambiguity wording) and the docs commit at HEAD.

Phase 5 checkpoint (live, 2026-09-18, API + worker + hosted planner, project `cc603f72` "Manual verification"): a clear request with an email clause produced a six-step renewal review that passed `validate`, with the email recorded as a rejected side effect; an ambiguous request ("check our contracts") produced a valid draft with three clarifying questions, each with a default; a prompt-injection request (developer mode, export tool, skip review, send documents) produced a DPA review with only registered tools and a human review, with the injection and side effects recorded by the screen. Every call was served by Nemotron 3 Super with no fallback.

## 2. How we work (user-approved process)

- **Fable (lead)** plans each phase in plan mode, writes a design doc (plan file under `C:\Users\shahm\.claude\plans\`), delegates implementation to subagents, reviews the safety-critical code personally, runs every gate, commits, pushes.
- **Subagents:** Sonnet for implementation; Opus for judgment-heavy generation (used for the synthetic dataset). Stage A runs independent agents in parallel with disjoint file ownership; Stage B integrates. Each prompt must include: repo path, files to read, exact interfaces, gates to run, "no commits, no attribution". Phase 5 pattern that worked: Fable writes the contract files (schemas, prompts, service signature) and commits them first, then two agents build against fixed interfaces; only one agent may run database tests.
- **Commits:** Conventional Commits, direct to `main`, no branches, **never any `Co-Authored-By` or Claude attribution** (contractual; the user had all history rewritten once to remove it). Push after each verified phase. Report hash, message, files, tests, push status at each checkpoint. Stop at phase checkpoints for user review.
- **Never present placeholders as features.** Unbuilt UI areas render a visibly labelled "not yet implemented" page naming the phase.
- Update `DECISIONS.md` (dated ADR) for material choices, `README.md` for setup changes, `docs/API_SPEC.md` and `docs/DATABASE_SPEC.md` when contracts change, `LOCAL_RUN.md` with manual test commands.

## 3. Stack and repository layout

- **Backend** `backend/` (uv project, Python 3.12 pinned, `uv.lock` committed): FastAPI + Pydantic v2 + pydantic-settings, SQLAlchemy 2 async + asyncpg, Alembic (migrations `0001` to `0003`), arq on Redis, PyJWT (Clerk RS256), aiobotocore (S3/MinIO), **langgraph 1.2** (Phase 5; pulls langchain-core, langsmith, httpx 0.28 beside httpx2, orjson, tenacity; pins websockets 16.x). One package `northforge` shared by API (`python -m northforge.api`) and worker (`python -m northforge.worker`). Modules: `core/`, `db/`, `schemas/`, `auth/`, `api/` (routes: system, projects, workflows incl. plan, catalog, documents, provider_status), `providers/`, `tools/`, `retrieval/`, `storage/`, `ingestion/`, `data/synthetic/`, `worker/` (jobs: `ping`, `ingest_synthetic_dataset`, `plan_workflow`), **`planner/`** (schema, prompts, screen, grounding, compile, graph, service).
- **Frontend** `frontend/`: Vite 7, React 19, TypeScript strict, Tailwind v4, shadcn-style primitives, React Router 7, Vitest. Only the Settings/status page is functional. Types generated from `frontend/openapi.json` into `src/lib/api-schema.d.ts` via `npm run generate:api` (CI fails on drift).
- **Data:** `backend/data/synthetic/` committed (48 docs: 17 msa, 8 policy, 7 sow, 6 order_form, 5 dpa, 5 nda; 15 vendors; 11 policy rules over 7 areas: renewal 3, data_protection 2, liability 2, payment, security, staffing, termination 1 each; ground truth; 36 retrieval eval cases). Regenerate: `uv run python -m northforge.data.synthetic --out data/synthetic --seed 20260916 --version v1`.
- **Infra:** `docker-compose.yml` runs Postgres 16 + pgvector (host port **5433**), Redis 7 (6379), MinIO (9000, console 9001). `.env.example` is the template; `.env` (untracked) mirrors it with `AUTH_MODE=dev` and a real `NVIDIA_API_KEY`.
- **CI** `.github/workflows/ci.yml`: backend job (ruff, mypy, alembic upgrade, pytest with Postgres/Redis/MinIO services, `NORTHFORGE_INTEGRATION=1`, dataset drift check), frontend job, contracts job (OpenAPI export + type generation drift).
- **Tests:** 689 backend (`NORTHFORGE_INTEGRATION=1 uv run pytest -q`: 686 pass, 3 live-gated skips, ~97 s, needs the three containers), 7 frontend. `tests/test_worker_plan_job.py` imports the API and worker entry points in a fresh interpreter, because an in-process import cannot catch a circular import. Test DB `northforge_test` is migrated once per session; each test runs in a rolled-back savepoint.

## 4. Key technical decisions (see ADRs for rationale)

- ADR-013/017: Clerk auth (`AUTH_MODE=clerk`) with a **dev mode** trusting `X-Dev-User`, rejected in production. Subject stored as `clerk_user_id` (`dev|<name>` in dev mode).
- ADR-018: other owners' resources return **404**, never 403.
- ADR-019: UUID keys, JSONB, string statuses with CHECK constraints, `alembic check` enforced.
- ADR-021: two validation layers. Parse layer accepts incomplete drafts; semantic layer (`validate_workflow`) runs on validate/approve and returns 422 `INVALID_WORKFLOW` with coded problems. References: `$input.<name>`, `$step.<id>.<field>`; referenced step must be a DAG ancestor.
- ADR-022: every tool call goes through `invoke_tool` (registered, allowlisted, args validated, timeout, output validated, trace). Tools: `search_documents`, `get_document_chunk`, `lookup_policy_rules`, all read-only.
- ADR-023: committed deterministic synthetic dataset. ADR-024: Postgres full-text retrieval with access-group filtering in SQL; outcomes `ok | insufficient_evidence | conflicting_evidence`. ADR-025: raw docs in S3/MinIO, chunks in Postgres.
- ADR-026: all model calls through `northforge/providers/` (`ModelRouter`: capability checks, retries, per-model circuit breaker, fallback on rate limit/outage/timeout only, Redis cache for deterministic requests outside production). `router.snapshot()` is the version/run model metadata; `ModelInvocationRecord` is the trace-safe per-call record.
- ADR-027: role defaults in `providers/model_catalog.json` (planner Nemotron 3 Super, extractor Nemotron 3.5 Lightning, drafter Kimi K3, evaluator DeepSeek V4 Flash, embedding Nemotron 3 Embed 1B, reranker Llama Nemotron Rerank). Retrieval is still lexical-only.
- **ADR-028 (Phase 5):** LangGraph planner `screen -> ground -> probe_tools -> propose -> compile -> (repair once) -> finalize`. The model emits a flat `PlannerProposal`; `compile.py` is the enforcer (registered tools only, `policies` empty, `approval_points` recomputed, `human_review` inserted before `finish` when missing and recorded as an assumption, then `parse_definition` + `validate_workflow`). `screen.py` records side-effect and injection phrases before the model runs; prompts label every untrusted block and break `</` so text cannot close its delimiter. Tool probes go through `invoke_tool` with the caller's real `ToolContext` (2 rounds, 4 calls) and are embedded as `<tool_result trust="untrusted">` blocks, never replayed as tool-role history into a structured call. Persistence: `workflow_versions.planner_output_json` (assumptions, questions, rejected actions, remaining problems, invocation and probe records, fallback count), `model_config_json = router.snapshot()`, `source_request`. Rejected or finish-less proposals persist nothing. Planning is async (`202 {job_id}`; `GET /api/jobs/{id}` result is `PlanJobResult`). A rejected proposal never triggers the repair call.
- Readiness semantics: Postgres/Redis/storage down → 503 `not_ready`; worker heartbeat missing → 200 `degraded`. Provider health never affects readiness.

## 5. Environment quirks (this machine)

- A native Windows PostgreSQL service occupies port 5432; Compose publishes Postgres on 5433. Never use 5432.
- Docker Desktop must be started manually (`Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"` works from PowerShell; the engine is reachable about 30 s later); the MCP Docker server fails until then.
- Memory pressure: only ~1 to 2 GB free with browser/WSL/IDE open. Start the API without the reloader (`uv run uvicorn northforge.api.main:create_app --factory --host 127.0.0.1 --port 8000`) when memory is tight.
- Git Bash: kill processes with `ps -W` + `taskkill //F //PID <winpid>`; the Bash tool truncates very large heredocs (keep file writes under ~150 lines per command; use the Write tool for bigger files).
- Two pytest sessions on the shared test DB deadlock (`migrated_database` downgrades/upgrades). Never run DB tests while a subagent runs them; pure unit sessions (e.g. `tests/planner` minus `test_service.py`) are safe to run alongside.
- Git config email `shahmir.ahmad2302@gmail.com` differs from the initial commit's noreply address; user should ensure both are linked to the GitHub account.
- Files in the working copy are CRLF; git normalises to LF on commit (harmless warnings on `git add`; `GIT_CONFIG_PARAMETERS="'core.safecrlf=false'"` silences them).
- Python `pathlib.read_text()` defaults to cp1252 on this machine: always pass `encoding="utf-8"` in scripts that touch the docs (they contain em dashes).
- `ruff format .` run by one subagent reformats everyone's files; harmless but tell agents to scope it.

## 6. Deferred items and follow-ups (tracked)

| # | Item | Why deferred | When to address |
|---|---|---|---|
| D1 | CI status on GitHub. | `gh` not installed; the user checks runs. Phase 4 run for `2c4c33f` confirmed green. | Ask at the start of each phase for the Phase 5 push. |
| D2 | Clerk not exercised against a live Clerk instance. | User has no Clerk app yet | **Before Phase 7 UI work:** create a Clerk application; wire `ClerkProvider`; manual login test with `AUTH_MODE=clerk`. |
| D3 | Frontend has only the Settings page. | Plan puts screens in Phase 7 | Phase 7. The planner's `planner_output` (assumptions, questions, rejected actions) is the input for the builder's proposal review pane. |
| D4 | Embeddings/pgvector adapters exist; retrieval lexical-only. | ADR-027: no evidence of lexical failure yet. | When a retrieval eval case fails hit@8 under honest phrasing. |
| D6 | `ALLOWED_TOOLS_BY_STEP` hard-coded; the planner's enforcer keys tool acceptance on registration only, not side-effect class. | Only read-only tools exist | When a draft-only tool is added: the enforcer must also check `side_effect_class` (ADR-028 revisit). |
| D7 | Nothing persists `ToolCallRecord`s or `ModelInvocationRecord`s to `trace_events`; the planner keeps its records inside `planner_output_json`. Runs/step runs/trace repositories exist with no API. | Runtime is Phase 6 | Phase 6 runner: persist tool calls, model calls, and step outputs as trace events; add run endpoints from `docs/API_SPEC.md`. |
| D8 | Job status relies on arq job keys with default expiry (result kept 1 h); a `rejected` planning outcome lives only in that transient job result. | MVP | Phase 6 when run jobs need durable status; consider a `jobs` table then, or persist rejected planning attempts. |
| D9 | Access groups only editable via `seed --grant-user`. | Simulated permissions | Phase 7 settings screen or Phase 9. |
| D10 | `step_catalog.config_schema` exposes the full step model. | Editor needs common fields anyway | Phase 7 editor. |
| D11 | Malformed synthetic document ingests with 2 warnings; long document has 47 sections. | Intentional fixtures | Phase 8 evaluation cases. |
| D12 | Windows worker cannot install POSIX signal handlers. | Platform | Phase 10 (Linux). |
| D13 | `docs/ARCHITECTURE.md` and `docs/TECH_STACK.md` not updated for MinIO, lexical-first retrieval, or the planner package. | Minor | Phase 11 doc sweep, or when touching those files. |
| D14 | `npm audit` shows 2 moderate advisories in transitive frontend deps. | Not blocking | Phase 9. |
| D15 | Frontend `Envelope<T>` is hand-written. | Cosmetic | Leave unless it drifts. |
| D16 | Nemotron 3 Super as planner primary. **Phase 5 evidence:** four live planner passes (the gated test plus three checkpoint requests; 13 calls: tool calls, structured calls, one repair) all served by Super, `fallback_count=0`, about 8 s per plan. The Phase 4 intermittency was not reproduced. | Keep Super; the fallback covers bad days | Re-check `fallback_count` in `planner_output_json` across Phase 7 manual runs; swap to Lightning via `NVIDIA_MODEL_PLANNER` if it is frequently non-zero. |
| D17 | Kimi K3 and DeepSeek V4 Flash take ~2 min per request. | Hosted quota | Phase 8 evaluation. |
| D18 | Commit `8b4d884` mixes Phase 4 docs with the thinking-off fix. | History not rewritten | None. |
| D19 | `planner_output_json` describes the proposal as generated; a `PATCH` of the draft does not refresh its `validation_errors`. | The version's warnings are refreshed by `validate`; the planner record is history | Phase 7: the editor should show `planner_output` as "what the planner said" and live validation separately. |
| D20 | The request screen is regex-based; false positives are possible on review language ("change the agreement unilaterally"). Mitigated by the `<excluded_actions>` wording that tells the model reviews of such actions stay in scope. | Deterministic screening is a safety floor, not a classifier | Phase 8: add planner evaluation cases for false positives/negatives and tune the phrase lists from evidence. |
| D21 | The planner is not idempotent: two identical plan requests create two workflows. `docs/API_SPEC.md` requires idempotency keys for runs, not planning. | MVP | Phase 6 alongside run idempotency if the UI double-submits. |
| D22 | Live planning showed the model sometimes references an extracted field directly (`$step.extract.vendor_name`) instead of `$step.extract.fields`. The system prompt and repair message now say so explicitly; the one-turn repair did not fix it before the change. | Fixed in the prompt; the validator catches any recurrence and the draft stays editable | Phase 8: add a planner evaluation case for reference errors and track the repair success rate. |

## 7. Phase 6 plan seed (next steps, exact)

Goal per `docs/IMPLEMENTATION_PLAN.md` Phase 6: a separate LangGraph runner that executes an **approved** workflow version reliably and observably: persist state at step boundaries, deterministic nodes for retrieval, validation, policy checks, formatting; model-driven nodes only for judgment (`extract_fields`, `classify`, `draft_summary`, `compare_policy` with `use_model_interpretation`); bounded retries with categorised errors; timeouts, cancellation, safe failure; checkpointed resume after worker interruption; trace events for every step and tool call; pause/resume for `human_review`; no side effects (read-only tools, drafts only).

What Phase 5 hands the runner:
- The graph idiom in `planner/graph.py` (`StateGraph`, closures over a deps dataclass, TypedDict state, nodes testable in isolation) and `tests/planner/conftest.py::build_mock_router` for scripted model runs.
- `ModelInvocationRecord` helpers (`graph.record_from_response`, `record_from_error`) and `ToolProbeRecord`; the runner should persist both kinds as `trace_events` (D7).
- Approved versions carry `definition_json` that passed `validate_workflow` with the live registry, so the runner may trust step wiring; it must still enforce the version's `tools` allowlist through `invoke_tool` (`ToolContext.allowed_tools`).
- Step output models in `schemas/step_outputs.py` are the contract for `$step.<id>.<field>` resolution (`schemas/refs.py::parse_ref`).
- Existing tables: `workflow_runs` (status transitions in `docs/DATABASE_SPEC.md`, partial unique index on `idempotency_key`), `step_runs` (unique on run, step, attempt), `trace_events` (sequence per run); repositories in `db/repositories/runs.py` with tests but no API.

Design points to settle in plan mode:
1. Checkpointing: langgraph's `AsyncPostgresSaver` (extra dependency `langgraph-checkpoint-postgres`, own tables) versus a NorthForge checkpointer that writes graph state into `workflow_runs.checkpoint_ref` + `step_runs` (keeps ADR-019 control of the schema). Recommendation to evaluate: custom `BaseCheckpointSaver` over the existing tables.
2. Human review: LangGraph `interrupt()` at `human_review` nodes with the run set to `paused`; `POST /api/runs/{id}/approve|reject` resumes with the decision as the step's output.
3. Model-driven step prompts: same boundary rules as the planner (system policy, step `instructions` as user-approved configuration, evidence chunks as untrusted `<evidence>` blocks with citations required by chunk id).
4. Run API from `docs/API_SPEC.md` (create with idempotency key, get, events list, pause/resume/approve/reject/retry/cancel; replay can wait for Phase 8).
5. Fixtures: run the `COMPLETE_DEFINITION` workflow end to end on the mock provider with scripted extractor/drafter outputs; kill-and-resume test; failure-policy tests (`fail_run`, `pause_for_review`, `skip`).
6. Delegation: Sonnet for graph + checkpointer, Sonnet for run API + jobs + tests; Fable reviews prompt boundaries, the tool allowlist enforcement, and the resume path.

Before starting Phase 6: start Docker Desktop, `docker compose up -d --wait`, confirm `git status` clean, ask the user for the CI result of the Phase 5 push (D1).

## 8. Quick commands

```bash
docker compose up -d --wait                      # postgres 5433, redis 6379, minio 9000/9001
cd backend && uv sync && uv run alembic upgrade head
uv run ruff format --check . && uv run ruff check . && uv run mypy
NORTHFORGE_INTEGRATION=1 uv run pytest -q         # 686 pass + 3 live-gated skips, ~97 s
uv run pytest -q tests/planner                    # offline planner tests only (~1 s)
NORTHFORGE_LIVE_MODELS=1 uv run pytest -q -s tests/planner/test_live_planner.py   # one live plan, prints fallback_count
uv run uvicorn northforge.api.main:create_app --factory --host 127.0.0.1 --port 8000
uv run python -m northforge.worker                # --check for liveness; python -m northforge.worker.ping for a round trip
uv run python -m northforge.ingestion.seed --project-id <uuid> [--grant-user "dev|alice" legal_restricted]
uv run python -m northforge.providers.smoke [--verbose] [--json]
curl -s -X POST http://127.0.0.1:8000/api/projects/<id>/workflows/plan -H "Content-Type: application/json" -H "X-Dev-User: alice" -d '{"request": "..."}'
curl -s http://127.0.0.1:8000/api/jobs/<job_id> -H "X-Dev-User: alice"
uv run python -m northforge.api.export_openapi && cd ../frontend && npm run generate:api
cd frontend && npm install && npm run dev         # http://localhost:5173/settings
```

Dev user `alice` (header `X-Dev-User: alice`) owns four projects in the local dev database, one seeded with the corpus; the Phase 5 checkpoint planned workflows into it.
