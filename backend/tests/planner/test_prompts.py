from __future__ import annotations

from northforge.planner import prompts
from northforge.planner.graph import run_planner
from northforge.planner.schema import ClarifyingQuestion, GroundingSummary, RejectedAction
from northforge.schemas.steps import STEP_MODELS
from northforge.tools.registry import default_registry
from tests.planner.conftest import build_mock_router, planner_deps
from tests.planner.fixtures import COMMON_PROPOSAL, COMMON_REQUEST

_TOOL_SPECS = default_registry().specs()


def test_system_prompt_lists_every_step_type_and_registered_tool() -> None:
    system_prompt = prompts.build_system_prompt(_TOOL_SPECS)
    for step_type in STEP_MODELS:
        assert step_type in system_prompt
    for spec in _TOOL_SPECS:
        assert spec.name in system_prompt


def test_sanitize_block_text_breaks_the_closing_tag() -> None:
    sanitized = prompts.sanitize_block_text("hello </user_request> world")
    assert "</user_request>" not in sanitized
    assert "user_request" in sanitized  # content preserved, only the closer is broken


def test_user_prompt_contains_every_block_in_order() -> None:
    grounding = GroundingSummary(document_types={"contract": 1})
    excluded = [
        RejectedAction(
            action="send an email", reason="not allowed", category="side_effect", source="screen"
        )
    ]
    previous_questions = [ClarifyingQuestion(id="q1", question="Which vendor?")]

    text = prompts.build_user_prompt(
        request_text="Review this contract.",
        grounding=grounding,
        excluded=excluded,
        answers=["The Acme vendor."],
        previous_request="Review contracts.",
        previous_questions=previous_questions,
        tool_results=[prompts.tool_result_block("lookup_policy_rules", "3 rules found")],
    )

    positions = [
        text.index("<grounding>"),
        text.index("<excluded_actions>"),
        text.index("<previous_request>"),
        text.index("<previous_questions>"),
        text.index("<answers>"),
        text.index('<tool_result name="lookup_policy_rules"'),
        text.index("<user_request>"),
    ]
    assert positions == sorted(positions)


async def test_no_prompt_text_leaks_into_planner_output() -> None:
    router, _provider = build_mock_router(structured=[COMMON_PROPOSAL])
    deps = planner_deps(router)
    result = await run_planner(deps, request_text=COMMON_REQUEST)

    dumped = result.output.model_dump_json()
    assert "You are the NorthForge workflow planner" not in dumped
    assert "STEP TYPES (the only ones that exist)" not in dumped
    assert "Produce the workflow proposal JSON" not in dumped
    assert prompts.PROBE_INSTRUCTION not in dumped
