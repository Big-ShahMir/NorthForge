"""Execution context and trace records for tool invocations.

Every tool implementation receives only a ``ToolContext``: it has no ambient
database session, no hidden document access, and no way to reach beyond the
caller's declared ``access_groups`` and ``allowed_tools``. Data access is
mediated entirely through ``retriever`` and ``rule_store`` -- built-in tools
never talk to a database, object store, or fixture corpus directly, only
through these two protocols, so swapping the fixture implementations for the
Postgres-backed ones (see ``build_tool_context``) changes nothing about tool
behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from northforge.retrieval.postgres import PostgresRetriever, SessionOrFactory
from northforge.retrieval.retriever import Retriever
from northforge.retrieval.rules import PolicyRuleStore, PostgresPolicyRuleStore

if TYPE_CHECKING:
    from northforge.db.models import User


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
    """Per-invocation scope: identity, data access, and permissions."""

    project_id: str
    user_id: str
    retriever: Retriever
    rule_store: PolicyRuleStore
    access_groups: frozenset[str] = field(default_factory=frozenset)
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    trace: Callable[[ToolCallRecord], None] | None = None


def build_tool_context(
    user: User,
    project_id: str,
    allowed_tools: frozenset[str],
    session_factory: SessionOrFactory,
    trace: Callable[[ToolCallRecord], None] | None = None,
) -> ToolContext:
    """Build a ``ToolContext`` wired to the real, Postgres-backed data access.

    ``session_factory`` is normally the app's session factory (each tool
    call opens and closes its own session); a single already-open
    ``AsyncSession`` is also accepted, for callers (tests) that need every
    query to run inside one shared transaction.
    """
    return ToolContext(
        project_id=str(project_id),
        user_id=str(user.id),
        retriever=PostgresRetriever(session_factory),
        rule_store=PostgresPolicyRuleStore(session_factory),
        access_groups=frozenset(str(group) for group in user.access_groups_json),
        allowed_tools=allowed_tools,
        trace=trace,
    )


__all__ = ["ToolCallRecord", "ToolContext", "build_tool_context"]
