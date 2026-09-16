from __future__ import annotations

from typing import Any

import pytest

from northforge.core.errors import InvalidWorkflowError
from northforge.schemas.workflow import WorkflowDefinition, validate_definition


def _base_definition(**overrides: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "schema_version": 1,
        "name": "Contract review",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve documents"},
            {"id": "finish", "type": "finish", "label": "Finish"},
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
        "tools": ["document_search"],
    }
    definition.update(overrides)
    return definition


VALID_FULL_EXAMPLE: dict[str, Any] = {
    "schema_version": 1,
    "name": "Contract review",
    "description": "Review a contract against policy and draft a summary.",
    "user_request": "Review this contract for compliance with our procurement policy.",
    "inputs": {"contract_id": {"type": "string"}},
    "steps": [
        {"id": "retrieve_documents", "type": "retrieve_documents", "label": "Retrieve documents"},
        {"id": "extract_fields", "type": "extract_fields", "label": "Extract fields"},
        {"id": "compare_policy", "type": "compare_policy", "label": "Compare against policy"},
        {"id": "draft_summary", "type": "draft_summary", "label": "Draft summary"},
        {
            "id": "human_review",
            "type": "human_review",
            "label": "Human review",
            "requires_approval": True,
        },
        {"id": "validate_output", "type": "validate_output", "label": "Validate output"},
        {"id": "finish", "type": "finish", "label": "Finish"},
    ],
    "edges": [
        {"source": "retrieve_documents", "target": "extract_fields"},
        {"source": "extract_fields", "target": "compare_policy"},
        {"source": "compare_policy", "target": "draft_summary"},
        {"source": "draft_summary", "target": "human_review"},
        {"source": "human_review", "target": "validate_output"},
        {"source": "validate_output", "target": "finish"},
    ],
    "tools": ["document_search"],
    "output_schema": {"type": "object"},
    "approval_points": ["human_review"],
    "policies": {},
}


def test_valid_full_example_produces_no_warnings() -> None:
    definition, warnings = validate_definition(VALID_FULL_EXAMPLE)

    assert isinstance(definition, WorkflowDefinition)
    assert warnings == []


def test_duplicate_step_ids_is_a_hard_error() -> None:
    raw = _base_definition(
        steps=[
            {"id": "retrieve", "type": "retrieve_documents", "label": "One"},
            {"id": "retrieve", "type": "extract_fields", "label": "Two"},
        ],
        edges=[],
    )

    with pytest.raises(InvalidWorkflowError) as excinfo:
        validate_definition(raw)

    assert excinfo.value.code == "INVALID_WORKFLOW"
    assert excinfo.value.status_code == 422
    assert any("duplicate step id" in str(item) for item in excinfo.value.details)


def test_unknown_edge_endpoint_is_a_hard_error() -> None:
    raw = _base_definition(edges=[{"source": "retrieve", "target": "does_not_exist"}])

    with pytest.raises(InvalidWorkflowError) as excinfo:
        validate_definition(raw)

    assert any("unknown" in str(item) for item in excinfo.value.details)


def test_unknown_approval_point_is_a_hard_error() -> None:
    raw = _base_definition(approval_points=["does_not_exist"])

    with pytest.raises(InvalidWorkflowError):
        validate_definition(raw)


def test_cycle_is_a_hard_error() -> None:
    raw = _base_definition(
        steps=[
            {"id": "a", "type": "retrieve_documents", "label": "A"},
            {"id": "b", "type": "extract_fields", "label": "B"},
        ],
        edges=[
            {"source": "a", "target": "b"},
            {"source": "b", "target": "a"},
        ],
    )

    with pytest.raises(InvalidWorkflowError) as excinfo:
        validate_definition(raw)

    assert any("cycle" in str(item) for item in excinfo.value.details)


def test_two_finish_steps_is_a_hard_error() -> None:
    raw = _base_definition(
        steps=[
            {"id": "finish_a", "type": "finish", "label": "Finish A"},
            {"id": "finish_b", "type": "finish", "label": "Finish B"},
        ],
        edges=[],
    )

    with pytest.raises(InvalidWorkflowError) as excinfo:
        validate_definition(raw)

    assert any("finish" in str(item) for item in excinfo.value.details)


def test_bad_step_id_pattern_is_a_hard_error() -> None:
    raw = _base_definition(
        steps=[{"id": "Not-Valid", "type": "retrieve_documents", "label": "Bad id"}],
        edges=[],
    )

    with pytest.raises(InvalidWorkflowError):
        validate_definition(raw)


def test_unknown_step_type_is_a_hard_error() -> None:
    raw = _base_definition(
        steps=[{"id": "step", "type": "not_a_real_type", "label": "Bad type"}],
        edges=[],
    )

    with pytest.raises(InvalidWorkflowError):
        validate_definition(raw)


def test_extra_top_level_field_is_rejected() -> None:
    raw = _base_definition(unexpected_field="not allowed")

    with pytest.raises(InvalidWorkflowError):
        validate_definition(raw)


def test_extra_step_field_is_forbidden() -> None:
    """Phase 2 removes the one Phase 1 leniency: unknown step fields now
    hard-fail instead of being silently accepted (DECISIONS.md ADR-021)."""
    raw = _base_definition(
        steps=[
            {
                "id": "retrieve",
                "type": "retrieve_documents",
                "label": "Retrieve documents",
                "bogus_field": "not a real field",
            },
            {"id": "finish", "type": "finish", "label": "Finish"},
        ]
    )

    with pytest.raises(InvalidWorkflowError):
        validate_definition(raw)


def test_no_steps_still_parses_and_warns_no_human_review() -> None:
    """Phase 2 drops the old free-text "workflow has no steps" warning; an
    empty workflow still parses, and the semantic layer's no_human_review
    warning (there being no steps at all implies no human_review step)
    still surfaces through validate_definition."""
    _definition, warnings = validate_definition({"schema_version": 1, "name": "Empty workflow"})

    assert any(warning.startswith("no_human_review:") for warning in warnings)


def test_no_finish_step_no_longer_warns_via_validate_definition() -> None:
    """no_finish_step is now a semantic *error* code (ADR-021), not a
    warning, so validate_definition -- which only ever surfaces warnings,
    never semantic errors -- silently omits it. The error itself is
    exercised directly against validate_workflow in
    test_workflow_validation.py::test_no_finish_step_error."""
    raw = {
        "schema_version": 1,
        "name": "No finish",
        "steps": [{"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve"}],
        "tools": ["document_search"],
    }

    _definition, warnings = validate_definition(raw)

    assert not any("finish" in warning for warning in warnings)


def test_no_human_review_step_warns() -> None:
    """The warning text is now the formatted semantic code, not free text."""
    _definition, warnings = validate_definition(_base_definition())

    assert any(warning.startswith("no_human_review:") for warning in warnings)


def test_unreachable_step_no_longer_warns_via_validate_definition() -> None:
    """unreachable_step is now a semantic *error* code (ADR-021); see
    test_workflow_validation.py::test_unreachable_step_error."""
    raw = _base_definition(
        steps=[
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve"},
            {"id": "orphan", "type": "extract_fields", "label": "Orphan"},
            {"id": "finish", "type": "finish", "label": "Finish"},
        ],
        edges=[{"source": "retrieve", "target": "finish"}],
    )

    _definition, warnings = validate_definition(raw)

    assert not any(warning.startswith("unreachable_step:") for warning in warnings)


def test_retrieve_documents_without_tools_no_longer_warns_via_validate_definition() -> None:
    """An undeclared tool is now the semantic *error* code tool_not_declared
    (ADR-021); see test_workflow_validation.py::test_tool_not_declared_error."""
    raw = _base_definition(tools=[])

    _definition, warnings = validate_definition(raw)

    assert not any("tool" in warning for warning in warnings)
