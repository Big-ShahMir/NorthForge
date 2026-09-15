# NorthForge Model Routing

## Objective

Use NVIDIA-hosted models efficiently while keeping NorthForge provider-agnostic, testable, and resilient to model availability or quota changes.

## Provider interface

Implement an internal `ModelProvider` interface with operations for `generate`, `generate_structured`, `tool_call`, `embed`, `rerank`, and `count_tokens` where supported. Each operation returns normalized output, usage metadata, model identity, latency, and warnings.

## Routing roles

| Role | Purpose | Selection principle |
|---|---|---|
| Planner | Natural language request to workflow JSON | Strong instruction following and structured output. |
| Extractor | Fields and classifications from evidence | Reliable schema adherence and low latency. |
| Drafter | Evidence-backed summary | Strong synthesis with citation preservation. |
| Evaluator | Independent grading or ambiguity review | Avoid using identical prompts/configuration to the system under test when possible. |
| Embedding | Retrieval vectors | Prefer a stable local or hosted embedding model with documented dimensions. |
| Reranker | Ordering retrieved passages | Use only if measurable retrieval improvement justifies it. |

Do not use multiple agents simply to consume more model endpoints. The number of model calls must be justified by quality, reliability, or latency evidence.

The corresponding configuration must explicitly support `NVIDIA_MODEL_PLANNER`, `NVIDIA_MODEL_EXTRACTION`, `NVIDIA_MODEL_DRAFTER`, `NVIDIA_MODEL_EVALUATOR`, `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`, and `RERANKER_PROVIDER`/`RERANKER_MODEL`. Embedding and reranking providers are independent of NVIDIA generation models and may be local.

## NVIDIA integration

Use the OpenAI-compatible NVIDIA endpoint through a server-side adapter. Model names and capabilities belong in configuration, not application code. Verify the selected model’s support for structured output, tool calls, context length, and rate limits before relying on it.

## Quotas and fallback

Treat hosted access as quota-limited even when advertised as free. Implement per-model concurrency limits, exponential backoff, request timeouts, circuit breaking for repeated failures, and fallback models only when their capabilities satisfy the operation. Do not use routing to evade provider restrictions. Cache deterministic development/evaluation requests where appropriate.

## Structured outputs

Every planner, extractor, classifier, and evaluator response must be parsed against a Pydantic schema. On malformed output, retry with a concise repair request at most once, then fail safely. Preserve the raw response only when safe and necessary for debugging.

## Prompt boundaries

System instructions define NorthForge policy. Workflow instructions are user-approved configuration. Retrieved documents and tool outputs are untrusted data. Delimit and label them. Never let retrieved text redefine tools, permissions, approval requirements, or output policy.

## Measurement

Record provider, model, operation, request ID if available, latency, input/output token usage if available, retry count, cache hit, and error category. Never log API keys or unnecessary raw sensitive content.

## Testing

Unit tests use a fake provider. Integration tests use a small controlled NVIDIA smoke test when credentials are available. Evaluation reports must record the exact model configuration. If a provider is unavailable, local mocks must still run all deterministic tests.

## Initial policy

Use one primary planning model, one efficient extraction model, and an independent evaluator model only when evaluation quality requires it. Use local embeddings initially if hosted embedding quotas or availability are uncertain. Keep the model assignment configurable so later experiments do not change workflow code.
