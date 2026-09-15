# NorthForge Security and Safety

## Scope statement

NorthForge is a portfolio MVP using synthetic data. It is not a security-certified, privacy-certified, legal, compliance, or production enterprise system. The implementation must demonstrate sound controls without making unsupported guarantees.

## Identity and authorization

Require authentication for project data. Every project, workflow, run, trace, document, and evaluation query must verify ownership or future explicit membership. Test cross-project access. Do not trust client-supplied owner IDs.

## Secrets

Provider and storage credentials are server-side environment variables. Never commit them, return them to the browser, place them in URLs, or write them to logs. Provide `.env.example` with placeholders only. Redact authorization headers and sensitive provider metadata.

## Tool safety

Register tools with explicit schemas, scopes, and side-effect classifications. MVP tools are read-only or draft-only. Validate arguments before invocation. Use allowlists for tools and data sources. Require human approval before consequential actions. Do not add arbitrary code execution or shell tools.

## Prompt-injection defenses

Treat retrieved documents and tool outputs as untrusted content. Delimit them, label their provenance, and keep system policy separate. A document cannot redefine tools, permissions, approval requirements, or output format. Include injection fixtures in evaluations.

## Data handling

Use synthetic or explicitly reusable data. Minimize raw prompt and document retention. Store references and redacted excerpts where possible. Keep object storage private. Document retention and deletion behavior. Do not ingest real contracts or personal data for the demo.

## Output safety

Validate structured outputs against schemas. Require citations for material claims. Detect missing or conflicting evidence and abstain or route to review. Do not display generated conclusions as verified until deterministic checks pass.

## Reliability controls

Use bounded retries, timeouts, checkpointing, idempotency keys, cancellation, and explicit terminal states. Keep retries safe by avoiding side effects. Log approvals, rejections, edits, retries, and workflow-version changes as audit events.

## Abuse and availability

Limit request size, workflow complexity, concurrent runs, and provider calls. Add backpressure and provider circuit breaking. Avoid exposing detailed internal errors. Rate-limit public endpoints if deployed openly.

## Review checklist

Before deployment, test authentication, authorization, secret exposure, prompt injection, malformed outputs, tool abuse, duplicate requests, worker termination, provider outage, and database failure. Record limitations in the README and do not call the MVP production-ready.
