"""In-memory tool registry: binds a ``ToolSpec`` to its callable implementation.

The registry is the single source of truth for which tools exist. It never
performs argument validation, permission checks, or failure classification
itself -- that is ``northforge.tools.invoke.invoke_tool``'s job. Keeping the
registry dumb means the same registry can back both the (Stage B) read-only
catalog endpoint and the invocation pipeline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from northforge.tools.spec import ToolSpec

if TYPE_CHECKING:
    from northforge.tools.context import ToolContext

ToolImplementation = Callable[[BaseModel, "ToolContext"], Awaitable[dict[str, Any]]]
"""A tool implementation: takes validated input and the call context, returns
a raw (pre-validation) JSON-compatible payload for the tool's output model."""


class DuplicateToolError(ValueError):
    """Raised by ``register`` when the tool name is already registered."""


class UnknownToolError(KeyError):
    """Raised by ``get`` when the tool name has not been registered."""


class ToolRegistry:
    """Holds registered tool specs and implementations, keyed by tool name."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._implementations: dict[str, ToolImplementation] = {}

    def register(self, spec: ToolSpec, implementation: ToolImplementation) -> None:
        if spec.name in self._specs:
            raise DuplicateToolError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        self._implementations[spec.name] = implementation

    def get(self, name: str) -> tuple[ToolSpec, ToolImplementation]:
        if name not in self._specs:
            raise UnknownToolError(name)
        return self._specs[name], self._implementations[name]

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())


def default_registry() -> ToolRegistry:
    """Build a fresh registry containing the three Phase 2 built-in tools."""
    from northforge.tools.builtin.get_document_chunk import (
        GET_DOCUMENT_CHUNK_SPEC,
        get_document_chunk,
    )
    from northforge.tools.builtin.lookup_policy_rules import (
        LOOKUP_POLICY_RULES_SPEC,
        lookup_policy_rules,
    )
    from northforge.tools.builtin.search_documents import (
        SEARCH_DOCUMENTS_SPEC,
        search_documents,
    )

    registry = ToolRegistry()
    registry.register(SEARCH_DOCUMENTS_SPEC, search_documents)
    registry.register(GET_DOCUMENT_CHUNK_SPEC, get_document_chunk)
    registry.register(LOOKUP_POLICY_RULES_SPEC, lookup_policy_rules)
    return registry


_cached_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Return the process-wide cached registry, building it on first use.

    Stage B stores this on ``app.state.tool_registry`` in ``create_app``.
    """
    global _cached_registry
    if _cached_registry is None:
        _cached_registry = default_registry()
    return _cached_registry
