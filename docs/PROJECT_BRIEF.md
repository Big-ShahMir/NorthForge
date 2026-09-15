# NorthForge Project Brief

## Product

NorthForge is a web application for designing, running, supervising, and improving reliable AI workflows. It is not merely a chatbot or autonomous agent. It combines a workflow builder, a stateful agent runtime, a supervision interface, and an evaluation loop.

## Initial vertical

The MVP focuses on **vendor contract and policy review** using synthetic or explicitly reusable documents. A user can ask NorthForge to extract terms from a contract, compare them against company policy, identify exceptions, and draft a review summary.

## Problem

AI systems can produce plausible but unsupported answers, call the wrong tools, lose state during long tasks, and fail without explaining what happened. Enterprise users need to configure workflows, inspect evidence and intermediate steps, intervene before consequential actions, recover from failures, and turn mistakes into durable tests.

## Target user

The primary user is an operations, procurement, compliance, or knowledge-work professional who needs help reviewing business documents but wants control over the process and the ability to verify the result. The secondary user is an AI engineer who needs to debug and improve workflow behavior.

## Core promise

NorthForge helps a user turn a plain-language automation request into a visible, editable, measurable workflow that produces evidence-backed results and improves from feedback.

## MVP user journey

1. Create a project.
2. Describe an automation in natural language.
3. Review and edit the generated workflow.
4. Approve and run it on controlled documents.
5. Inspect sources, steps, tools, outputs, and errors.
6. Approve, correct, retry, or rerun the result.
7. Label failures and promote them to regression cases.
8. Compare evaluation results across workflow versions.

## Goals

- Demonstrate productized agent engineering.
- Make workflow state and model behavior inspectable.
- Measure real task success, groundedness, reliability, latency, and cost.
- Demonstrate human-in-the-loop control and failure recovery.
- Publish a reproducible open-source foundation with synthetic data.

## Non-goals

NorthForge will not be a general-purpose automation platform, legal-advice product, certified compliance product, unrestricted autonomous action agent, multi-tenant enterprise platform, or model-training system. MVP tools are read-only or draft-only.

## Success criteria

A new user can complete the contract-review journey without database access. A reviewer can reconstruct every run from trace events. A failed run can be labeled and replayed. At least three failure-driven changes produce measurable before/after improvement. The repository can be run by another engineer from documented commands.

## Product principles

Prefer deterministic code for permissions, validation, routing, calculations, and state transitions. Use models where judgment is valuable. Show evidence instead of hiding it. Fail explicitly and safely. Keep claims proportional to the test data. Optimize for one excellent workflow rather than feature breadth.

## Required documentation

The implementation must follow `PRODUCT_SPEC.md`, `ARCHITECTURE.md`, `WORKFLOW_SPEC.md`, `MODEL_ROUTING.md`, `DATABASE_SPEC.md`, `UI_UX_SPEC.md`, `EVALUATION_SPEC.md`, `API_SPEC.md`, `SECURITY_AND_SAFETY.md`, `TESTING_STRATEGY.md`, `DEPLOYMENT.md`, `IMPLEMENTATION_PLAN.md`, and `CLAUDE_CODE_INSTRUCTIONS.md`.
