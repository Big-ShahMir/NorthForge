# NorthForge Deployment

## Environments

Support local development, a test/demo deployment, and future production-like deployment. The demo environment uses synthetic data only.

## Local setup

Provide a documented setup using Docker Compose for PostgreSQL and Redis/queue. The API, worker, and frontend should run with one documented command or a small number of explicit commands. Include migration, seed-data, and test commands.

## Services

Render deployment should contain:

- Web service: FastAPI and compiled frontend.
- Background worker: queue consumer for LangGraph runs, ingestion, replay, and evaluation.
- Managed PostgreSQL: durable application state.
- Redis-like queue: asynchronous jobs and short-lived coordination.
- S3-compatible object storage: documents and artifacts.

Keep services in the same region where possible. Use private/internal database connections. Do not put provider keys in frontend variables.

## Configuration

Required configuration is listed in `TECH_STACK.md`. Maintain `.env.example` with descriptions but no secrets. Validate required variables at startup and return a useful readiness failure rather than a cryptic runtime exception.

## Migrations and seeds

Run migrations as a controlled deployment step. Seed only synthetic, deterministic data. Make seed operations idempotent. Document reset behavior and never run destructive resets automatically against a shared database.

## Health and operations

Expose `/health` for liveness and `/ready` for dependency readiness. Worker logs must include job ID, run ID, workflow version, step ID, status, duration, and error category without secrets. Configure log retention and basic alerting where available.

## Deployment checklist

1. Build and test locally.
2. Build the frontend and backend artifacts.
3. Configure environment variables.
4. Provision database and queue.
5. Run migrations.
6. Deploy web service and worker.
7. Verify health and readiness.
8. Run synthetic seed and smoke workflow.
9. Verify async worker processing.
10. Confirm provider credentials are not exposed to the client.
11. Record deployment URL and known limitations.

## Rollback

Keep the previous deploy available. Roll back application code before changing database schema when possible. Use additive migrations and avoid irreversible schema changes in the MVP. If a workflow version is incompatible, preserve old versions and stop new runs rather than silently rewriting them.

## Hosting limitations

Document sleep behavior, quotas, worker availability, database limits, provider rate limits, and object-storage assumptions. Do not claim high availability, compliance, or unlimited capacity.
