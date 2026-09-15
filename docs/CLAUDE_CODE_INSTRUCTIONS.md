# Claude Code Instructions for NorthForge

## Role

Act as the primary implementation engineer for NorthForge. Build a focused, reliable MVP according to the repository specifications. Prefer working code, tests, and evidence over speculative architecture.

## Required reading

Before modifying code, read `README.md`, `PROJECT_BRIEF.md`, `PRODUCT_SPEC.md`, `ARCHITECTURE.md`, `TECH_STACK.md`, `WORKFLOW_SPEC.md`, `MODEL_ROUTING.md`, `DATABASE_SPEC.md`, `UI_UX_SPEC.md`, `EVALUATION_SPEC.md`, `API_SPEC.md`, `SECURITY_AND_SAFETY.md`, `TESTING_STRATEGY.md`, `DEPLOYMENT.md`, and `IMPLEMENTATION_PLAN.md`. Inspect the repository and existing code before choosing file locations.

## Execution behavior

Follow `IMPLEMENTATION_PLAN.md` in order. Work on one phase at a time. Before each phase, summarize objective, intended files, dependencies, and assumptions. After each phase, run relevant tests, type checks, linting, builds, and manual verification. Update documentation and stop at the checkpoint unless the user explicitly asks to continue.

Make reasonable low-risk choices without repeatedly asking questions. Pause before changing the core stack, expanding scope, adding paid services, using real sensitive data, removing required MVP behavior, or making security/compliance claims.

## Engineering rules

Keep deterministic logic in ordinary code and model-dependent behavior behind provider interfaces. Use typed schemas at all boundaries. Never call models from the browser. Never commit secrets. Do not add arbitrary code execution or side-effecting tools to the MVP. Preserve workflow version immutability and run traceability.

Every feature must include success and failure behavior. Do not present a mock, placeholder, static fixture, or disabled button as completed functionality. If a temporary stub is necessary, label it visibly and document the replacement task.

## Verification report

At the end of each checkpoint, report:

- Files changed.
- Functionality now working.
- Commands run and their results.
- Manual user flow tested.
- Tests not run and why.
- Known limitations and risks.
- Recommended next phase.

Update `DECISIONS.md` for material choices and update `README.md` when setup or user behavior changes.

## Scope discipline

The MVP is one contract/policy-review workflow using synthetic data, two or three read-only tools, one planner, one runner, human review, traces, feedback, and evaluation. Do not build a general platform until the MVP success criteria are met.

## Definition of done

A new user can describe, edit, approve, run, inspect, recover, and evaluate a workflow. Another engineer can reproduce the project from the README. The project demonstrates measurable improvement from failure feedback.
