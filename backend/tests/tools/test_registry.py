from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from northforge.tools.registry import (
    DuplicateToolError,
    ToolRegistry,
    UnknownToolError,
    default_registry,
    get_tool_registry,
)
from northforge.tools.spec import ToolSpec


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _noop(args: BaseModel, context: object) -> dict[str, object]:
    return {}


def _spec(name: str = "dummy_tool") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="A dummy tool for registry tests.",
        input_model=_Args,
        output_model=_Result,
        side_effect_class="read_only",
        access_scope="project_documents",
        kind="lookup",
    )


def test_register_and_get_round_trips() -> None:
    registry = ToolRegistry()
    spec = _spec()

    registry.register(spec, _noop)

    got_spec, got_impl = registry.get("dummy_tool")
    assert got_spec is spec
    assert got_impl is _noop


def test_names_and_specs_reflect_registrations() -> None:
    registry = ToolRegistry()
    registry.register(_spec("tool_a"), _noop)
    registry.register(_spec("tool_b"), _noop)

    assert registry.names() == frozenset({"tool_a", "tool_b"})
    assert {spec.name for spec in registry.specs()} == {"tool_a", "tool_b"}


def test_duplicate_name_rejected() -> None:
    registry = ToolRegistry()
    registry.register(_spec("dummy_tool"), _noop)

    with pytest.raises(DuplicateToolError):
        registry.register(_spec("dummy_tool"), _noop)


def test_unknown_name_rejected() -> None:
    registry = ToolRegistry()

    with pytest.raises(UnknownToolError):
        registry.get("does_not_exist")


def test_default_registry_registers_the_three_builtin_tools() -> None:
    registry = default_registry()

    assert registry.names() == frozenset(
        {"search_documents", "get_document_chunk", "lookup_policy_rules"}
    )
    for spec in registry.specs():
        assert spec.side_effect_class == "read_only"


def test_get_tool_registry_returns_a_cached_singleton() -> None:
    first = get_tool_registry()
    second = get_tool_registry()

    assert first is second
    assert first.names() == default_registry().names()
