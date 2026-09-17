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

## Implementation (Phase 4)

`northforge.providers` implements this document. See ADR-026 and ADR-027 in `DECISIONS.md`.

| Piece | Where | Notes |
|---|---|---|
| Provider protocol and types | `providers/base.py`, `providers/types.py` | `generate`, `generate_structured`, `tool_call`, `embed`, `rerank`, `count_tokens`. Responses carry model, provider, latency, usage, request id, warnings, cache hit, attempts, fallback flag. |
| Error taxonomy | `providers/errors.py` | `PROVIDER_RATE_LIMITED` (429), `PROVIDER_UNAVAILABLE` (503), `PROVIDER_NOT_CONFIGURED` (503), `PROVIDER_REQUEST_REJECTED` (502), `PROVIDER_MALFORMED_OUTPUT` (502), `PROVIDER_CAPABILITY_MISMATCH` (500). Only rate limits, outages, and timeouts are retried or fall back. |
| Capability registry | `providers/capabilities.py`, `providers/model_catalog.json` | Per-model modality, structured-output mode, tool support, reasoning toggle, context window, embedding dimensions, plus `default_routes`. Override the file with `MODEL_CAPABILITIES_FILE`. |
| NVIDIA adapter | `providers/nvidia.py` | Chat and embeddings on `NVIDIA_BASE_URL`; reranking on `NVIDIA_RERANK_BASE_URL` (`/retrieval/<model>/reranking`). Credentials only from `NVIDIA_API_KEY`; never logged. |
| Structured output | `providers/structured.py` | Parse against the Pydantic schema; one repair request on failure; then `PROVIDER_MALFORMED_OUTPUT`. |
| Router | `providers/router.py` | Role to primary plus fallbacks, capability check per call, per-provider and per-model concurrency, retries with backoff and `Retry-After`, circuit breaker per model. `snapshot()` is the JSON recorded as run and version model metadata. |
| Cache | `providers/cache.py` | Redis, deterministic requests only (temperature 0 or `deterministic=True`), on outside production, bypassed on Redis errors. |
| Mock provider | `providers/mock.py` | Scripted responses and failure injection (timeout, 429, 5xx, auth, malformed JSON, bad request). `MODEL_PROVIDER=mock`, rejected in production. |
| Status | `GET /api/provider-status` | In-memory state only; never spends quota. |
| Live verification | `python -m northforge.providers.smoke` | One minimal call per role; records which structured-output mode each model accepts. |

### Configured roles

| Role | Default model | Fallback | Structured output | Tools |
|---|---|---|---|---|
| Planner | `nvidia/nemotron-3-super-120b-a12b` | `nvidia/nemotron-3.5-lightning-30b-a3b` | required | required |
| Extractor | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nemotron-3-super-120b-a12b` | required | optional |
| Drafter | `moonshotai/kimi-k3` | `nvidia/nemotron-3-super-120b-a12b` | optional | optional |
| Evaluator | `deepseek-ai/deepseek-v4-flash-0731` | `nvidia/nemotron-3.5-lightning-30b-a3b` | required | optional |
| Embedding | `nvidia/nemotron-3-embed-1b` (2048 dimensions) | none | n/a | n/a |
| Reranker | `nvidia/llama-nemotron-rerank-vl-1b-v2` | none | n/a | n/a |

Environment overrides: `NVIDIA_MODEL_PLANNER`, `NVIDIA_MODEL_EXTRACTION`, `NVIDIA_MODEL_DRAFTER`, `NVIDIA_MODEL_EVALUATOR` and the matching `*_FALLBACKS` (comma-separated); `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`; `RERANKER_PROVIDER`/`RERANKER_MODEL` (`none` disables a role). Startup fails, naming every problem, when a configured model is not in the catalog or lacks what its role needs.

### Hosted endpoint limits (verified 2026-09-16)

- Free tier: 40 requests per minute per API key across all models; 429 with an optional `Retry-After`. There is no self-service increase. Keep `MODEL_PROVIDER_CONCURRENCY` low and rely on the cache for repeated evaluation runs.
- Embeddings: `input_type` must be `query` or `passage`; the model returns only 2048 dimensions; inputs are validated up to 4096 tokens.
- Reranking: a different host (`ai.api.nvidia.com`), query plus passage up to 10,240 tokens.
- Some models (DeepSeek V4 Flash was reported) need access enabled per account; the smoke command surfaces this as `PROVIDER_REQUEST_REJECTED`.

### Verified models

Run `cd backend && uv run python -m northforge.providers.smoke` with `NVIDIA_API_KEY` set and paste the table it prints here.

Verified on 2026-09-16 (three full runs plus a direct diagnostic; latencies are single-request wall times on the free tier):

| Role | Model | Verified | Structured mode | Tool calls | Latency (ms) |
|---|---|---|---|---|---|
| planner | nvidia/nemotron-3-super-120b-a12b | yes, intermittent | json_schema (2 of 3), prompt_only otherwise | 1 of 3 (HTTP 500 otherwise) | 550 to 1700 |
| extractor | nvidia/nemotron-3.5-lightning-30b-a3b | yes | json_schema | yes | 506 (thinking off) |
| drafter | moonshotai/kimi-k3 | yes | json_schema | yes | 127663 |
| evaluator | deepseek-ai/deepseek-v4-flash-0731 | yes | json_schema | yes | 119387 |
| embedding | nvidia/nemotron-3-embed-1b | yes (2048 dims) | - | - | 372 |
| reranker | nvidia/llama-nemotron-rerank-vl-1b-v2 | yes | - | - | 263 |

Findings that shaped the catalog:

- Hosted Nemotron models return HTTP 500 or an empty tool call when thinking is on; the catalog sets `default_reasoning: false` for both and the adapter sends `enable_thinking: false` unless a request asks otherwise. Lightning's structured call dropped from about 30 s to 0.5 s with thinking off.
- Nemotron 3 Super is intermittent on the hosted endpoint: one structured call in three timed out at 60 s, and two tool calls in three failed with HTTP 500 regardless of the thinking setting. Both failures are fallback-eligible, so planner calls land on Lightning when Super misbehaves; if Phase 5 fixtures show a high fallback rate, swap the planner primary to Lightning (one environment variable).
- `nvext.guided_json` is rejected by the hosted endpoint with HTTP 400 (unknown field); `json_schema` is the working structured mode for every generation model.
- Kimi K3 and DeepSeek V4 Flash queue for about two minutes per request on the free tier even for one-sentence replies (`completion_tokens` under 100). The catalog gives them a 240 s timeout. Evaluation runs that use them will be slow; the deterministic-request cache makes repeated runs cheap.

## Initial policy

Use one primary planning model, one efficient extraction model, and an independent evaluator model only when evaluation quality requires it. Use local embeddings initially if hosted embedding quotas or availability are uncertain. Keep the model assignment configurable so later experiments do not change workflow code.
