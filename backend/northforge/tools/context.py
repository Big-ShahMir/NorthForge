"""Execution context and trace records for tool invocations.

Every tool implementation receives only a ``ToolContext``: it has no ambient
database session, no hidden document access, and no way to reach beyond the
caller's declared ``access_groups`` and ``allowed_tools``. Fixture-backed
tools filter their corpus by ``access_groups`` internally (see
``northforge.tools.fixtures.corpus.visible_chunks``); they never return or
confirm the existence of data outside it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallRecord:
    """A trace record for one tool invocation.

    ``outcome`` is ``"ok"`` on success or the ``ToolError.code`` on failure.
    This never carries raw exception text or tracebacks -- only the stable
    code, the validated arguments, and timing -- so it is safe to persist or
    display.
    """

    name: str
    args: dict[str, Any]
    outcome: str
    duration_ms: float
    attempt: int = 1


@dataclass(frozen=True)
class ToolContext:
    """Per-invocation scope: identity, data visibility, and permissions."""

    project_id: str
    user_id: str
    access_groups: frozenset[str] = field(default_factory=frozenset)
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    trace: Callable[[ToolCallRecord], None] | None = None
