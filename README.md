# NorthForge

NorthForge is a web application for designing, running, supervising, and improving reliable AI workflows. The MVP focuses on one vertical: vendor contract and policy review over synthetic documents. A user describes an automation in plain language, edits the proposed workflow, runs it against controlled documents, inspects evidence and every intermediate step, intervenes where needed, and turns failures into regression tests.

This is a portfolio MVP built on synthetic data. It is not a security-certified, compliance, legal-advice, or production system.

## Status

Phase 0 (repository bootstrap) is complete. The API, worker, database, queue, and frontend shell start from documented commands and each component's health is independently verifiable. Product behaviour (projects, workflows, runs, evaluations) begins in Phase 1. See `docs/IMPLEMENTATION_PLAN.md`.

## Architecture in one paragraph

A React and TypeScript web app talks only to a FastAPI API. The API is stateless and enqueues long-running work onto a Redis-backed queue (arq). A separate Python worker runs LangGraph planner, runner, and evaluator graphs and persists checkpoints and trace events to PostgreSQL. Models are reached only from the backend through a provider interface, with NVIDIA's OpenAI-compatible endpoint as the default. The API and worker share one Python package, `northforge`. Full details live in `docs/ARCHITECTURE.md`; material choices are recorded in `DECISIONS.md`.

## Repository layout

```
backend/           Python package `northforge` (API, worker, core), tests, uv project
frontend/          Vite + React + TypeScript app, Vitest tests
docs/              Product, architecture, workflow, data, evaluation, and security specifications
.github/workflows  CI: lint, type-check, test, build (no secrets required)
docker-compose.yml PostgreSQL 16 (pgvector) and Redis 7 for local development
.env.example       Every environment variable with placeholders
DECISIONS.md       Architecture decision records
```

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.12 (managed by uv) |
| uv | 0.8 or newer |
| Node.js / npm | 22 / 10 |
| Docker with Compose v2 | any recent |

## Local setup

1. Copy the environment template and keep the defaults for local development:

   ```bash
   cp .env.example .env
   ```

2. Start PostgreSQL and Redis:

   ```bash
   docker compose up -d --wait
   ```

   The database is published on host port 5433 by default so a locally installed PostgreSQL on 5432 does not intercept connections. Override with `POSTGRES_PORT` and `DATABASE_URL` in `.env`.

3. Install dependencies, apply database migrations, and start the API (terminal 1):

   ```bash
   cd backend
   uv sync
   uv run alembic upgrade head
   uv run python -m northforge.api
   ```

   Re-run `uv run alembic upgrade head` after pulling any change that adds a migration.

4. Start the worker (terminal 2):

   ```bash
   cd backend
   uv run python -m northforge.worker
   ```

5. Install and start the frontend (terminal 3):

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

   Open http://localhost:5173. The Settings page shows live API, PostgreSQL, Redis, and worker status. The Vite dev server proxies `/api`, `/health`, and `/ready` to the API on port 8000.

## Verifying each component

| Component | Command | Expected |
|---|---|---|
| API liveness | `curl -i http://127.0.0.1:8000/health` | `200` with `{"data":{"status":"ok",...}}` |
| Dependencies | `curl -i http://127.0.0.1:8000/ready` | `200` and `"status":"ready"`; `"degraded"` if the worker is stopped; `503` if PostgreSQL or Redis is down |
| Worker liveness | `cd backend && uv run python -m northforge.worker --check` | exit code 0 and a health-check log line |
| Queue round trip | `cd backend && uv run python -m northforge.worker.ping hello` | JSON echo containing `"echo": "hello"` |
| Frontend | open http://localhost:5173/settings | status cards, or a visible error card if the API is down |

Every API response uses the envelope `{"data", "error", "request_id"}`. The `X-Request-ID` header is echoed or generated and appears in every JSON log line for that request.

## Authentication

`AUTH_MODE` selects how the API authenticates a caller:

- `AUTH_MODE=dev` (the `.env.example` default) trusts an `X-Dev-User: <any string>` request header instead of verifying a real session token. Each distinct header value is its own user, created automatically on first use. This mode is for local development only and startup fails if `APP_ENV=production`.
- `AUTH_MODE=clerk` verifies a Clerk-issued session token from `Authorization: Bearer <token>` against Clerk's published JWKS, and requires `CLERK_JWKS_URL` and `CLERK_ISSUER` to be set.

With the API running locally in dev mode, create a project as user `alice`:

```bash
curl -s -X POST http://127.0.0.1:8000/api/projects \
  -H "Content-Type: application/json" \
  -H "X-Dev-User: alice" \
  -d '{"name": "Vendor contract review", "description": "MVP demo project"}'
```

A request for the same project id with a different `X-Dev-User` value (a different user) gets `404 NOT_FOUND`, not `403 FORBIDDEN` -- see `docs/API_SPEC.md`.

## Quality checks

Backend (from `backend/`):

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q                                  # unit tests, no services needed
NORTHFORGE_INTEGRATION=1 uv run pytest -q         # also probes live PostgreSQL and Redis
```

Repository and API tests migrate and use a separate database so they never touch development data. It defaults to `DATABASE_URL` with `_test` appended to the database name (e.g. `northforge_test`); set `NORTHFORGE_TEST_DATABASE_URL` to override it explicitly (CI does this to target its own service container).

Frontend (from `frontend/`):

```bash
npm run format:check
npm run lint
npm run typecheck
npm run test
npm run build
```

CI runs the same commands on every push and pull request without provider credentials.

## Configuration

All settings are read from environment variables (or `.env` at the repository root). Missing or invalid required variables stop startup with a message that names every problem. Secrets are `SecretStr` values and never appear in logs, error responses, or the frontend bundle. See `.env.example` for the full list; later-phase variables (Clerk, NVIDIA, S3) are optional until their phase.

## Resetting local data

`docker compose down` keeps data. `docker compose down -v` destroys the local PostgreSQL volume. Never run the latter against a shared database.

## Known limitations

- Projects and workflows have a backend API (see `docs/API_SPEC.md`) but no frontend UI yet; there is no run or evaluation support yet either. Sidebar entries for those areas render a clearly labelled "not yet implemented" page.
- The readiness endpoint opens a fresh PostgreSQL connection per probe; a pooled engine arrives with the Phase 1 data layer.
- Windows: the worker cannot install POSIX signal handlers, so stop it with Ctrl+C in its terminal; in-flight jobs are not gracefully drained.
- Docker Desktop must be running before `docker compose up`.
