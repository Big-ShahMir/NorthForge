from __future__ import annotations

from typing import Any

import pytest

from northforge.planner.graph import run_planner
from northforge.planner.schema import MAX_TOOL_PROBE_CALLS
from northforge.providers.errors import ProviderError
from northforge.providers.mock import MockCall, ScriptedFailure
from northforge.providers.types import GenerationRequest, ToolCallRequest
from tests.planner.conftest import build_mock_router, planner_deps
from tests.planner.fixtures import (
    AMBIGUOUS_PROPOSAL,
    AMBIGUOUS_REQUEST,
    COMMON_PROPOSAL,
    COMMON_REQUEST,
    INJECTION_PROPOSAL,
    INJECTION_REQUEST,
    INVALID_PROPOSAL,
    REJECTED_PROPOSAL,
    UNSUPPORTED_TOOL_PROPOSAL,
)


def _structured_calls(provider: Any) -> list[MockCall]:
    return [call for call in provider.calls if call.operation == "generate_structured"]


async def test_common_request_is_proposed_with_zero_errors() -> None:
    router, provider = build_mock_router(structured=[COMMON_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert result.output.outcome == "proposed"
    assert result.output.validation_errors == []
    assert result.definition is not None
    assert len(_structured_calls(provider)) == 1


async def test_ambiguous_request_surfaces_clarifying_questions() -> None:
    router, _provider = build_mock_router(structured=[AMBIGUOUS_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=AMBIGUOUS_REQUEST)

    assert result.output.outcome == "needs_clarification"
    assert len(result.output.clarifying_questions) == 2
    assert result.definition is not None


async def test_invalid_then_repaired_uses_exactly_two_structured_calls() -> None:
    router, provider = build_mock_router(structured=[INVALID_PROPOSAL, COMMON_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert len(_structured_calls(provider)) == 2
    assert result.output.repair_attempted is True
    assert result.output.validation_errors == []
    assert result.definition is not None


async def test_repair_failure_keeps_errors_and_definition() -> None:
    router, provider = build_mock_router(structured=[INVALID_PROPOSAL, INVALID_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert len(_structured_calls(provider)) == 2
    assert result.output.repair_attempted is True
    assert result.output.validation_errors != []
    assert result.definition is not None


async def test_unsupported_tool_is_stripped_from_the_definition() -> None:
    router, _provider = build_mock_router(structured=[UNSUPPORTED_TOOL_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text="Review the contract, then email the vendor.")

    assert result.definition is not None
    assert "send_email" not in result.definition.tools
    categories = {item.category for item in result.output.rejected_actions}
    assert "unsupported_tool" in categories
    assert "side_effect" in categories


async def test_prompt_injection_is_recorded_and_review_is_enforced() -> None:
    router, _provider = build_mock_router(structured=[INJECTION_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=INJECTION_REQUEST)

    assert result.definition is not None
    assert "export_documents" not in result.definition.tools
    for tool_name in result.definition.tools:
        assert tool_name in {"search_documents", "get_document_chunk", "lookup_policy_rules"}
    step_types = {step.type for step in result.definition.steps}
    assert "human_review" in step_types
    categories = {item.category for item in result.output.rejected_actions}
    assert "prompt_injection" in categories
    injection_items = [
        item for item in result.output.rejected_actions if item.category == "prompt_injection"
    ]
    assert all(item.source == "screen" for item in injection_items)


async def test_provider_outage_propagates() -> None:
    router, _provider = build_mock_router(
        structured=[COMMON_PROPOSAL], failures=[ScriptedFailure(kind="rate_limited", times=10)]
    )
    deps = planner_deps(router)
    with pytest.raises(ProviderError):
        await run_planner(deps, request_text=COMMON_REQUEST)


# -- tool probing --------------------------------------------------------------


async def test_tool_probe_records_calls_and_feeds_the_propose_prompt() -> None:
    router, provider = build_mock_router(
        structured=[COMMON_PROPOSAL],
        tool_calls=[
            [
                ToolCallRequest(
                    id="call_1", name="lookup_policy_rules", arguments={"policy_area": "renewal"}
                )
            ],
            [
                ToolCallRequest(
                    id="call_2", name="search_documents", arguments={"query": "acme contract"}
                )
            ],
        ],
    )
    deps = planner_deps(router, probe_tools=True)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert len(result.output.tool_probes) == 2
    assert {record.outcome for record in result.output.tool_probes} == {"ok"}
    assert {record.name for record in result.output.tool_probes} == {
        "lookup_policy_rules",
        "search_documents",
    }

    structured_calls = _structured_calls(provider)
    assert len(structured_calls) == 1
    propose_request = structured_calls[0].request
    assert isinstance(propose_request, GenerationRequest)
    propose_user_message = propose_request.messages[1].content
    assert 'name="lookup_policy_rules"' in propose_user_message
    assert 'name="search_documents"' in propose_user_message


async def test_tool_probe_call_cap_is_respected() -> None:
    too_many_calls = [
        ToolCallRequest(id=f"call_{i}", name="lookup_policy_rules", arguments={"policy_area": "x"})
        for i in range(MAX_TOOL_PROBE_CALLS + 3)
    ]
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL], tool_calls=[too_many_calls])
    deps = planner_deps(router, probe_tools=True)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert len(result.output.tool_probes) == MAX_TOOL_PROBE_CALLS
    assert any("cap" in warning for warning in result.output.warnings)


async def test_unknown_tool_name_is_recorded_not_raised() -> None:
    router, _provider = build_mock_router(
        structured=[COMMON_PROPOSAL],
        tool_calls=[[ToolCallRequest(id="call_1", name="delete_everything", arguments={})]],
    )
    deps = planner_deps(router, probe_tools=True)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert len(result.output.tool_probes) == 1
    assert result.output.tool_probes[0].outcome == "unknown_tool"
    assert any("unknown tool" in warning for warning in result.output.warnings)


async def test_tool_call_provider_error_warns_and_planning_continues() -> None:
    router, _provider = build_mock_router(
        structured=[COMMON_PROPOSAL],
        failures=[ScriptedFailure(kind="rate_limited", operation="tool_call", times=10)],
    )
    deps = planner_deps(router, probe_tools=True)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert result.output.outcome == "proposed"
    assert result.definition is not None
    assert any("tool probe skipped" in warning for warning in result.output.warnings)


async def test_proposal_without_finish_step_is_not_persistable() -> None:
    no_finish = {
        **COMMON_PROPOSAL,
        "steps": [step for step in COMMON_PROPOSAL["steps"] if step["type"] != "finish"],
        "edges": [edge for edge in COMMON_PROPOSAL["edges"] if edge["target"] != "finish"],
    }
    router, _provider = build_mock_router(structured=[no_finish, no_finish])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    assert result.definition is not None
    assert "no_finish_step" in {problem.code for problem in result.output.validation_errors}
    assert result.persistable is False


async def test_rejected_proposal_skips_repair_and_is_not_persistable() -> None:
    router, provider = build_mock_router(structured=[REJECTED_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text="Negotiate a new contract with the vendor.")

    assert len(_structured_calls(provider)) == 1
    assert result.output.outcome == "rejected"
    assert result.output.repair_attempted is False
    assert result.persistable is False
    assert {item.category for item in result.output.rejected_actions} >= {"out_of_scope"}
