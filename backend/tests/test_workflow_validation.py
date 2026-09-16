"""One test per semantic error and warning code (ADR-021), plus a full,
zero-problem contract-review definition shared with Stage B."""

from __future__ import annotations

from typing import Any

from northforge.schemas.workflow import WorkflowDefinition
from northforge.schemas.workflow_validation import Problem, validate_workflow
from tests.fixtures_workflows import COMPLETE_DEFINITION, REAL_TOOL_NAMES


def _validate(raw: dict[str, Any], *, known_tools: set[str] | None = None) -> Any:
    definition = WorkflowDefinition.model_validate(raw)
    return validate_workflow(definition, known_tools=known_tools)


def _has(problems: list[Problem], code: str) -> bool:
    return any(problem.code == code for problem in problems)


# --- Complete definition: zero errors, zero warnings ------------------------


def test_complete_definition_has_no_errors_or_warnings_without_registry() -> None:
    report = _validate(COMPLETE_DEFINITION, known_tools=None)

    assert report.ok
    assert report.errors == []
    assert report.warnings == []


def test_complete_definition_has_no_errors_or_warnings_with_real_registry() -> None:
    report = _validate(COMPLETE_DEFINITION, known_tools=set(REAL_TOOL_NAMES))

    assert report.ok
    assert report.errors == []
    assert report.warnings == []


# --- Error codes --------------------------------------------------------------


def test_missing_required_config_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [{"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve"}],
    }

    report = _validate(raw)

    assert _has(report.errors, "missing_required_config")


def test_unknown_input_reference_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {
                "id": "retrieve",
                "type": "retrieve_documents",
                "label": "Retrieve",
                "query": "$input.missing",
            },
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.retrieve.chunks"},
            },
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
        "inputs": {},
    }

    report = _validate(raw)

    assert _has(report.errors, "unknown_input_reference")


def test_reference_not_ancestor_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"},
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.retrieve.chunks"},
            },
        ],
        "edges": [],
    }

    report = _validate(raw)

    assert _has(report.errors, "reference_not_ancestor")


def test_reference_unknown_field_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"},
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.retrieve.not_a_field"},
            },
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
    }

    report = _validate(raw)

    assert _has(report.errors, "reference_unknown_field")


def test_tool_not_declared_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"}
        ],
        "tools": [],
    }

    report = _validate(raw)

    assert _has(report.errors, "tool_not_declared")


def test_tool_not_registered_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"}
        ],
        "tools": ["search_documents"],
    }

    report = _validate(raw, known_tools={"lookup_policy_rules"})

    assert _has(report.errors, "tool_not_registered")


def test_tool_not_allowed_for_step_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {
                "id": "retrieve",
                "type": "retrieve_documents",
                "label": "Retrieve",
                "query": "q",
                "tool": "lookup_policy_rules",
            },
        ],
        "tools": ["lookup_policy_rules"],
    }

    report = _validate(raw)

    assert _has(report.errors, "tool_not_allowed_for_step")


def test_approval_point_mismatch_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "inputs": {"a": {"type": "string"}},
        "steps": [
            {"id": "review", "type": "human_review", "label": "Review"},
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$input.a"},
            },
        ],
        "edges": [{"source": "review", "target": "finish"}],
        "approval_points": [],
    }

    report = _validate(raw)

    assert _has(report.errors, "approval_point_mismatch")


def test_no_finish_step_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"}
        ],
    }

    report = _validate(raw)

    assert _has(report.errors, "no_finish_step")


def test_unreachable_step_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"},
            {
                "id": "orphan",
                "type": "extract_fields",
                "label": "Orphan",
                "evidence": "$step.retrieve.chunks",
                "fields": [{"name": "f", "description": "d", "type": "string"}],
            },
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.retrieve.chunks"},
            },
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
    }

    report = _validate(raw)

    assert _has(report.errors, "unreachable_step")


def test_finish_result_reference_invalid_error() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"},
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "literal_not_a_ref"},
            },
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
    }

    report = _validate(raw)

    assert _has(report.errors, "finish_result_reference_invalid")


def test_invalid_reference_syntax_error() -> None:
    """Not one of the design's listed codes: a malformed ``$``-prefixed value
    (see ``schemas/refs.parse_ref``) must still surface as a problem rather
    than crash validation, so it gets its own code."""
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {
                "id": "retrieve",
                "type": "retrieve_documents",
                "label": "Retrieve",
                "query": "$input",
            },
        ],
    }

    report = _validate(raw)

    assert _has(report.errors, "invalid_reference_syntax")


# --- Warning codes -------------------------------------------------------------


def test_no_human_review_warning() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve", "query": "q"},
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.retrieve.chunks"},
            },
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
        "tools": ["search_documents"],
    }

    report = _validate(raw)

    assert report.ok
    assert _has(report.warnings, "no_human_review")


def test_no_retrieval_before_extraction_warning() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "inputs": {"doc": {"type": "document_id"}},
        "steps": [
            {
                "id": "extract",
                "type": "extract_fields",
                "label": "Extract",
                "evidence": "$input.doc",
                "fields": [{"name": "f", "description": "d", "type": "string"}],
            },
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.extract.fields"},
            },
        ],
        "edges": [{"source": "extract", "target": "finish"}],
    }

    report = _validate(raw)

    assert _has(report.warnings, "no_retrieval_before_extraction")


def test_draft_without_citations_warning() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "inputs": {"x": {"type": "string"}},
        "steps": [
            {
                "id": "draft",
                "type": "draft_summary",
                "label": "Draft",
                "sources": ["$input.x"],
                "require_citations": False,
            },
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"y": "$step.draft.summary_markdown"},
            },
        ],
        "edges": [{"source": "draft", "target": "finish"}],
    }

    report = _validate(raw)

    assert _has(report.warnings, "draft_without_citations")


def test_high_retry_budget_warning() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Test",
        "steps": [
            {
                "id": "retrieve",
                "type": "retrieve_documents",
                "label": "Retrieve",
                "query": "q",
                "timeout_seconds": 600,
                "retry_policy": {"max_attempts": 5},
            },
            {
                "id": "finish",
                "type": "finish",
                "label": "Finish",
                "result": {"x": "$step.retrieve.chunks"},
            },
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
    }

    report = _validate(raw)

    assert _has(report.warnings, "high_retry_budget")
