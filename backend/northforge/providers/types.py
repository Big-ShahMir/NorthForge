"""Provider-agnostic request and response types for model calls.

Every model operation in NorthForge -- plain generation, structured
generation, tool selection, embedding, reranking -- is expressed with the
types here, so the planner, runner, and evaluator never see a provider's
wire format. They are Pydantic models rather than dataclasses because the
caching layer serialises responses to JSON and replays them verbatim.

Nothing here carries credentials. ``ModelInvocationRecord`` is the only
type meant to be persisted as a trace event; it mirrors
``northforge.tools.context.ToolCallRecord`` and never includes prompt or
completion text.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.tools.spec import ToolSpec

ModelRole = Literal["planner", "extractor", "drafter", "evaluator", "embedding", "reranker"]
MODEL_ROLES: tuple[ModelRole, ...] = (
    "planner",
    "extractor",
    "drafter",
    "evaluator",
    "embedding",
    "reranker",
)
GENERATION_ROLES: frozenset[str] = frozenset({"planner", "extractor", "drafter", "evaluator"})

Operation = Literal["generate", "generate_structured", "tool_call", "embed", "rerank"]
MessageRole = Literal["system", "user", "assistant", "tool"]
Modality = Literal["generation", "embedding", "rerank"]
StructuredOutputMode = Literal["json_schema", "nvext_guided_json", "prompt_only"]
EmbeddingInputType = Literal["query", "passage"]
ToolChoice = Literal["auto", "none", "required"]


class ToolCallRequest(BaseModel):
    """A tool the model asked to run, with already-decoded JSON arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One chat turn.

    ``tool_calls`` is set only on assistant turns that requested tools (some
    providers require the complete assistant turn to be echoed back on the
    next request); ``tool_call_id`` and ``name`` are set only on ``tool``
    turns carrying a tool result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    """A tool offered to the model: name, purpose, and a JSON Schema for its arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    description: str
    parameters: dict[str, Any]


def tool_definition_from_spec(spec: ToolSpec) -> ToolDefinition:
    """Build a ``ToolDefinition`` from a registered ``ToolSpec``'s input model."""
    return ToolDefinition(
        name=spec.name,
        description=spec.description,
        parameters=spec.input_model.model_json_schema(),
    )


class GenerationRequest(BaseModel):
    """Input to ``generate``, ``generate_structured``, and ``tool_call``.

    ``deterministic`` controls cache eligibility; when left ``None`` it is
    derived from ``temperature == 0``. ``reasoning`` of ``None`` means "the
    model's default"; providers only forward it when the model's
    capabilities say the toggle is supported. ``metadata`` is for log
    correlation only (role, step id) and must never contain secrets.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: list[Message] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    tools: list[ToolDefinition] = Field(default_factory=list)
    tool_choice: ToolChoice = "auto"
    reasoning: bool | None = None
    deterministic: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def is_deterministic(self) -> bool:
        if self.deterministic is not None:
            return self.deterministic
        return self.temperature == 0.0


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    texts: list[str] = Field(min_length=1)
    input_type: EmbeddingInputType = "passage"
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1)
    passages: list[str] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class Usage(BaseModel):
    """Token accounting as reported by the provider; ``None`` when not reported."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class _ResponseBase(BaseModel):
    """Fields every response carries, whichever operation produced it."""

    model_config = ConfigDict(extra="forbid")

    model: str
    provider: str
    latency_ms: float = Field(ge=0.0)
    request_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    warnings: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    attempts: int = Field(default=1, ge=1)
    fallback_used: bool = False


class ModelResponse[T](_ResponseBase):
    """Result of a generation call.

    For structured generation ``content`` always holds the raw text that
    ``parsed`` was validated from, so a cached response can be re-parsed
    against the schema on replay.
    """

    content: str | None = None
    parsed: T | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str | None = None


class EmbeddingResponse(_ResponseBase):
    vectors: list[list[float]]
    dimensions: int = Field(ge=1)


class RerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    score: float


class RerankResponse(_ResponseBase):
    """``rankings`` is ordered by descending ``score``; ``index`` points into the request."""

    rankings: list[RerankResult]


class ModelInvocationRecord(BaseModel):
    """Trace-safe record of one routed model call.

    ``outcome`` is ``"ok"`` or the ``ProviderError.code`` that ended the
    call. Never carries prompt text, completion text, or credentials, so it
    is safe to persist as a trace event or display in a run view.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Operation
    provider: str
    model: str
    outcome: str
    latency_ms: float = Field(ge=0.0)
    role: ModelRole | None = None
    attempts: int = Field(default=1, ge=1)
    cache_hit: bool = False
    fallback_used: bool = False
    usage: Usage = Field(default_factory=Usage)
    request_id: str | None = None


__all__ = [
    "GENERATION_ROLES",
    "MODEL_ROLES",
    "EmbeddingInputType",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "GenerationRequest",
    "Message",
    "MessageRole",
    "Modality",
    "ModelInvocationRecord",
    "ModelResponse",
    "ModelRole",
    "Operation",
    "RerankRequest",
    "RerankResponse",
    "RerankResult",
    "StructuredOutputMode",
    "ToolCallRequest",
    "ToolChoice",
    "ToolDefinition",
    "Usage",
    "tool_definition_from_spec",
]
