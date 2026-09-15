# NorthForge Evaluation Specification

## Purpose

Evaluation measures whether NorthForge completes useful workflows reliably. It must expose failure modes and guide product changes, not merely produce a single quality score.

## Dataset

Create 25–50 synthetic contract/policy cases. Each case contains input documents, access metadata, user request, expected fields, required evidence, expected policy decision, allowed tools, and known edge conditions. Version the dataset and keep generation deterministic.

Include normal cases, missing evidence, conflicting policies, ambiguous requests, long documents, malformed documents, unauthorized documents, and adversarial instructions embedded in documents.

## Metrics

| Area | Metrics |
|---|---|
| Workflow outcome | Task success rate, completion rate, abstention correctness |
| Extraction | Field-level precision, recall, and schema-valid rate |
| Retrieval | Relevant passage precision, recall, and citation coverage |
| Groundedness | Supported-claim rate and unsupported-claim rate |
| Policy | Decision accuracy and exception-detection recall |
| Tool use | Valid-call rate, wrong-tool rate, blocked unauthorized-call rate |
| Human control | Approval compliance, intervention rate, review burden |
| Reliability | Retry recovery, unrecoverable failure, duplicate-effect rate, replay success |
| Operations | p50/p95 latency, token usage, estimated cost, provider error rate |

## Grading

Use deterministic graders for schema validity, required fields, citation references, access boundaries, tool contracts, policy rules, and exact structured values. Use model-assisted grading only for bounded semantic judgments, with a documented rubric and a human-reviewed calibration subset. Preserve raw outputs and grader explanations.

## Failure taxonomy

Use `retrieval_miss`, `bad_context`, `unsupported_claim`, `wrong_tool`, `malformed_tool_arguments`, `state_loss`, `ambiguous_intent`, `policy_misclassification`, `provider_error`, `timeout`, and `evaluator_disagreement`. Allow one primary and optional secondary category.

## Evaluation runs

An evaluation run references one immutable workflow version, dataset version, model configuration, and evaluator configuration. Each case produces a result, metrics, trace reference, status, and failure category. Runs must be repeatable enough to compare configurations; report variance when model behavior is nondeterministic.

## Feedback loop

A reviewer labels a production-style run, records corrected output or expected behavior, selects a failure category, and optionally promotes it to an evaluation case. The system must preserve the originating run and identify the dataset version that changed.

## Improvement evidence

The final project must show at least three iterations. For each, record the failure, hypothesis, change, affected component, before metrics, after metrics, regressions, and decision. Improvements can come from better retrieval, prompt/context changes, workflow constraints, tool validation, or UI intervention.

## Evaluation limitations

Do not claim that synthetic data predicts enterprise performance. Report dataset size, grader limitations, model versions, hardware, quota effects, and untested edge cases. A high score without representative failure analysis is insufficient.
