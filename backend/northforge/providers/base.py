"""The ``ModelProvider`` protocol every adapter implements.

A provider knows how to talk to one backend (NVIDIA's hosted endpoint, the
in-process mock) for a *named model*; it does not choose models, retry,
break circuits, or cache -- those are the router's and the wrappers' jobs.
A provider raises only ``northforge.providers.errors.ProviderError``
subclasses; any other exception escaping an adapter is a bug.

``NotConfiguredProvider`` is what the factory installs when no credentials
are present, so the API and worker still start and every model call fails
with a clear ``PROVIDER_NOT_CONFIGURED`` instead of an attribute error.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from northforge.providers.errors import ProviderNotConfiguredError
from northforge.providers.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    ModelResponse,
    RerankRequest,
    RerankResponse,
    ToolDefinition,
)


@runtime_checkable
class ModelProvider(Protocol):
    """One backend, addressed by model id."""

    @property
    def name(self) -> str:
        """Stable provider identifier recorded in responses and traces, e.g. ``"nvidia"``."""
        ...

    async def generate(self, request: GenerationRequest, model: str) -> ModelResponse[None]:
        """Free-text completion. ``response.content`` is the assistant text."""
        ...

    async def generate_structured[T: BaseModel](
        self, request: GenerationRequest, model: str, schema: type[T]
    ) -> ModelResponse[T]:
        """Completion constrained to ``schema``; ``content`` keeps the raw text."""
        ...

    async def tool_call(
        self, request: GenerationRequest, model: str, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        """Let the model pick tools; ``tool_calls`` holds the decoded requests (may be empty)."""
        ...

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        """One vector per input text, in order."""
        ...

    async def rerank(self, request: RerankRequest, model: str) -> RerankResponse:
        """Score every passage against the query; rankings sorted by descending score."""
        ...

    def count_tokens(self, text: str, model: str) -> int | None:
        """Exact token count when the backend can provide one, otherwise ``None``."""
        ...


def estimate_tokens(text: str) -> int:
    """Conservative fallback for backends without a tokenizer: about four characters per token."""
    return max(1, (len(text) + 3) // 4)


class NotConfiguredProvider:
    """Stands in for a real provider when no credentials are configured."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def name(self) -> str:
        return "not_configured"

    def _fail(self, model: str) -> ProviderNotConfiguredError:
        return ProviderNotConfiguredError(self._reason, provider=self.name, model=model)

    async def generate(self, request: GenerationRequest, model: str) -> ModelResponse[None]:
        raise self._fail(model)

    async def generate_structured[T: BaseModel](
        self, request: GenerationRequest, model: str, schema: type[T]
    ) -> ModelResponse[T]:
        raise self._fail(model)

    async def tool_call(
        self, request: GenerationRequest, model: str, tools: list[ToolDefinition]
    ) -> ModelResponse[None]:
        raise self._fail(model)

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        raise self._fail(model)

    async def rerank(self, request: RerankRequest, model: str) -> RerankResponse:
        raise self._fail(model)

    def count_tokens(self, text: str, model: str) -> int | None:
        return None


__all__ = ["ModelProvider", "NotConfiguredProvider", "estimate_tokens"]
