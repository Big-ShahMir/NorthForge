from __future__ import annotations

from northforge.planner.compile import compile_proposal
from northforge.planner.schema import PlannerProposal
from northforge.schemas.steps import ComparePolicyStep
from northforge.tools.registry import default_registry
from tests.planner.fixtures import (
    COMMON_PROPOSAL,
    INJECTION_PROPOSAL,
    INVALID_PROPOSAL,
    REJECTED_PROPOSAL,
    UNSUPPORTED_TOOL_PROPOSAL,
)

KNOWN_TOOLS = default_registry().names()


def _proposal(raw: dict[str, object]) -> PlannerProposal:
    return PlannerProposal.model_validate(raw)


# -- rule 1: unregistered tools are dropped and recorded ----------------------


def test_unregistered_tool_is_dropped_and_recorded() -> None:
    result = compile_proposal(
        _proposal(UNSUPPORTED_TOOL_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="review"
    )
    assert result.definition is not None
    assert "send_email" not in result.definition.tools
    assert "search_documents" in result.definition.tools
    unsupported = [item for item in result.rejected if item.category == "unsupported_tool"]
    assert len(unsupported) == 1
    assert unsupported[0].action == "send_email"
    assert unsupported[0].source == "enforcer"


def test_step_config_tool_left_unchanged_for_validator_to_report() -> None:
    """A step's own ``config['tool']`` is never rewritten by the enforcer."""
    raw = dict(COMMON_PROPOSAL)
    raw = {**raw, "tools": ["search_documents", "lookup_policy_rules"]}
    steps = [dict(step) for step in raw["steps"]]
    steps[2] = {**steps[2], "config": {**steps[2]["config"], "tool": "unregistered_tool"}}
    raw["steps"] = steps
    result = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="review")
    assert result.definition is not None
    compare_step = next(step for step in result.definition.steps if step.id == "compare")
    assert isinstance(compare_step, ComparePolicyStep)
    assert compare_step.tool == "unregistered_tool"
    assert any(error.code == "tool_not_registered" for error in result.errors)


# -- rule 2: raw step shape, policies, approval points, user_request ----------


def test_common_fields_inside_config_are_dropped_and_warned() -> None:
    raw = {**COMMON_PROPOSAL}
    steps = [dict(step) for step in raw["steps"]]
    steps[0] = {**steps[0], "config": {**steps[0]["config"], "timeout_seconds": 999}}
    raw["steps"] = steps
    result = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="review")
    assert result.definition is not None
    retrieve_step = next(step for step in result.definition.steps if step.id == "retrieve")
    assert retrieve_step.timeout_seconds != 999
    assert any(warning.code == "config_common_field_ignored" for warning in result.warnings)


def test_policies_are_always_empty_and_user_request_is_the_screened_text() -> None:
    result = compile_proposal(
        _proposal(COMMON_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="screened text"
    )
    assert result.definition is not None
    assert result.definition.policies == {}
    assert result.definition.user_request == "screened text"


def test_name_falls_back_when_proposal_name_is_blank() -> None:
    raw = {**COMMON_PROPOSAL, "name": ""}
    result = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="review")
    assert result.definition is not None
    assert result.definition.name == "Planned workflow"


def test_inputs_skip_blank_names_with_a_warning() -> None:
    raw = {
        **COMMON_PROPOSAL,
        "inputs": [
            {"name": "", "type": "string", "description": "blank", "required": True},
            {"name": "vendor", "type": "string", "description": "ok", "required": True},
        ],
    }
    result = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="review")
    assert result.definition is not None
    assert set(result.definition.inputs) == {"vendor"}
    assert any(warning.code == "input_missing_name" for warning in result.warnings)


def test_approval_points_are_never_trusted_from_the_model() -> None:
    """``approval_points`` is not a ``PlannerProposal`` field; it is always recomputed."""
    result = compile_proposal(
        _proposal(COMMON_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="review"
    )
    assert result.definition is not None
    assert result.definition.approval_points == ["review"]

    raw = {**COMMON_PROPOSAL}
    steps = [dict(step) for step in raw["steps"]]
    # A second step claims requires_approval; approval_points must follow it.
    steps[3] = {**steps[3], "requires_approval": True}
    raw["steps"] = steps
    result2 = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="review")
    assert result2.definition is not None
    assert result2.definition.approval_points == ["draft", "review"]


# -- rule 3: supervision default ----------------------------------------------


def test_human_review_is_inserted_before_finish_when_missing() -> None:
    result = compile_proposal(
        _proposal(INJECTION_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="export everything"
    )
    assert result.definition is not None
    step_types = [step.type for step in result.definition.steps]
    assert "human_review" in step_types
    assert any("human review" in assumption.lower() for assumption in result.assumptions_added)
    review_step = next(step for step in result.definition.steps if step.type == "human_review")
    assert review_step.requires_approval is True
    finish_step = next(step for step in result.definition.steps if step.type == "finish")
    edge_targets = {
        edge.target for edge in result.definition.edges if edge.source == review_step.id
    }
    assert finish_step.id in edge_targets
    # No edge should still point directly at finish from a non-review step.
    for edge in result.definition.edges:
        if edge.target == finish_step.id:
            assert edge.source == review_step.id


def test_human_review_insertion_uses_a_unique_id() -> None:
    raw = {**INJECTION_PROPOSAL}
    steps = [dict(step) for step in raw["steps"]]
    steps.append(
        {
            "id": "review",
            "type": "retrieve_documents",
            "label": "Not actually a review step",
            "config": {"query": "x"},
        }
    )
    raw["steps"] = steps
    result = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="x")
    assert result.definition is not None
    ids = [step.id for step in result.definition.steps]
    assert ids.count("review") == 1
    assert "review_2" in ids


def test_no_insertion_when_human_review_already_present() -> None:
    result = compile_proposal(
        _proposal(COMMON_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="review"
    )
    assert result.definition is not None
    assert result.assumptions_added == []
    review_steps = [step for step in result.definition.steps if step.type == "human_review"]
    assert len(review_steps) == 1


# -- rule 4: parse errors ------------------------------------------------------


def test_parse_error_becomes_problems_with_no_definition() -> None:
    raw = {**COMMON_PROPOSAL}
    steps = [dict(step) for step in raw["steps"]]
    steps[0] = {**steps[0], "id": "Not Valid Id!"}
    raw["steps"] = steps
    result = compile_proposal(_proposal(raw), known_tools=KNOWN_TOOLS, user_request="review")
    assert result.definition is None
    assert result.errors
    assert all(error.code == "parse_error" for error in result.errors)


# -- rule 5: semantic validation -----------------------------------------------


def test_semantic_errors_are_reported_but_definition_still_parses() -> None:
    result = compile_proposal(
        _proposal(INVALID_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="review"
    )
    assert result.definition is not None
    assert any(error.code == "reference_not_ancestor" for error in result.errors)


def test_common_proposal_compiles_with_zero_errors_and_warnings() -> None:
    result = compile_proposal(
        _proposal(COMMON_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="review"
    )
    assert result.definition is not None
    assert result.errors == []
    assert result.warnings == []


# -- rejected outcome still runs the full pipeline -----------------------------


def test_rejected_outcome_still_runs_the_pipeline() -> None:
    result = compile_proposal(
        _proposal(REJECTED_PROPOSAL), known_tools=KNOWN_TOOLS, user_request="review"
    )
    # No finish step at all: the pipeline still parses (0 steps is legal) and
    # validate_workflow reports the missing finish step as an error.
    assert result.definition is not None
    assert any(error.code == "no_finish_step" for error in result.errors)
