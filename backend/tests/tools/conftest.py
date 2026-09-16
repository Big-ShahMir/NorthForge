from __future__ import annotations

from collections.abc import Callable

import pytest

from northforge.retrieval.fixture import FixtureRetriever
from northforge.retrieval.rules import FixturePolicyRuleStore
from northforge.tools.context import ToolCallRecord, ToolContext
from northforge.tools.registry import ToolRegistry, default_registry

PROCUREMENT = frozenset({"procurement"})
PROCUREMENT_AND_LEGAL = frozenset({"procurement", "legal_restricted"})
ALL_TOOL_NAMES = frozenset({"search_documents", "get_document_chunk", "lookup_policy_rules"})


def make_context(
    *,
    access_groups: frozenset[str] = PROCUREMENT,
    allowed_tools: frozenset[str] = ALL_TOOL_NAMES,
    trace: Callable[[ToolCallRecord], None] | None = None,
) -> ToolContext:
    return ToolContext(
        project_id="proj_test",
        user_id="user_test",
        retriever=FixtureRetriever(),
        rule_store=FixturePolicyRuleStore(),
        access_groups=access_groups,
        allowed_tools=allowed_tools,
        trace=trace,
    )


class RecordingTracer:
    """A trace callback that stores every ``ToolCallRecord`` it receives."""

    def __init__(self) -> None:
        self.records: list[ToolCallRecord] = []

    def __call__(self, record: ToolCallRecord) -> None:
        self.records.append(record)


@pytest.fixture
def registry() -> ToolRegistry:
    return default_registry()


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer()
