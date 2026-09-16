from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from northforge.schemas.step_outputs import StepType
from northforge.schemas.steps import (
    STEP_MODELS,
    ClassifyStep,
    ComparePolicyStep,
    DraftSummaryStep,
    ExtractFieldsStep,
    FinishStep,
    HumanReviewStep,
    RetrieveDocumentsStep,
    ValidateOutputStep,
    WorkflowStep,
)
from northforge.schemas.workflow import WorkflowDefinition, validate_definition
from tests.fixtures_workflows import COMPLETE_DEFINITION

_STEP_ADAPTER: TypeAdapter[WorkflowStep] = TypeAdapter(WorkflowStep)

_STEP_TYPES: list[StepType] = [
    "retrieve_documents",
    "extract_fields",
    "compare_policy",
    "classify",
    "draft_summary",
    "human_review",
    "validate_output",
    "finish",
]


def _minimal_step(step_type: StepType) -> dict[str, Any]:
    return {"id": "s", "type": step_type, "label": "Step"}


@pytest.mark.parametrize("step_type", _STEP_TYPES)
def test_each_step_type_parses_with_defaults(step_type: StepType) -> None:
    step = STEP_MODELS[step_type].model_validate(_minimal_step(step_type))
    assert step.id == "s"
    assert step.type == step_type
    assert step.timeout_seconds == 120
    assert step.retry_policy.max_attempts == 2
    assert step.requires_approval is False
    assert step.failure_policy == "fail_run"


def test_retrieve_documents_defaults() -> None:
    step = RetrieveDocumentsStep.model_validate(_minimal_step("retrieve_documents"))
    assert step.tool == "search_documents"
    assert step.query == ""
    assert step.document_types == []
    assert step.vendor is None
    assert step.limit == 8
    assert step.min_results == 1


def test_extract_fields_defaults() -> None:
    step = ExtractFieldsStep.model_validate(_minimal_step("extract_fields"))
    assert step.evidence == ""
    assert step.fields == []
    assert step.require_citations is True


def test_compare_policy_defaults() -> None:
    step = ComparePolicyStep.model_validate(_minimal_step("compare_policy"))
    assert step.fields == ""
    assert step.policy_area == ""
    assert step.tool == "lookup_policy_rules"
    assert step.rules == []
    assert step.use_model_interpretation is False


def test_classify_defaults() -> None:
    step = ClassifyStep.model_validate(_minimal_step("classify"))
    assert step.evidence == ""
    assert step.categories == []


def test_draft_summary_defaults() -> None:
    step = DraftSummaryStep.model_validate(_minimal_step("draft_summary"))
    assert step.sources == []
    assert step.require_citations is True
    assert step.max_words == 400


def test_human_review_defaults() -> None:
    step = HumanReviewStep.model_validate(_minimal_step("human_review"))
    assert step.show == []
    assert step.decisions == ["approve", "reject", "edit"]


def test_validate_output_defaults() -> None:
    step = ValidateOutputStep.model_validate(_minimal_step("validate_output"))
    assert step.target == ""
    assert step.checks == ["schema", "required_fields", "citations"]


def test_finish_defaults() -> None:
    step = FinishStep.model_validate(_minimal_step("finish"))
    assert step.result == {}


@pytest.mark.parametrize("step_type", _STEP_TYPES)
def test_each_step_type_forbids_extra_fields(step_type: StepType) -> None:
    raw = _minimal_step(step_type)
    raw["not_a_real_field"] = "nope"
    with pytest.raises(ValidationError):
        STEP_MODELS[step_type].model_validate(raw)


def test_discriminator_error_is_readable() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _STEP_ADAPTER.validate_python({"id": "s", "type": "not_a_real_type", "label": "Step"})

    message = str(excinfo.value)
    assert "type" in message
    assert "not_a_real_type" in message


def test_missing_discriminator_error_is_readable() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _STEP_ADAPTER.validate_python({"id": "s", "label": "Step"})

    message = str(excinfo.value)
    assert "type" in message.lower()


@pytest.mark.parametrize("timeout_seconds", [0, -1, 601, 1000])
def test_timeout_seconds_out_of_bounds_rejected(timeout_seconds: int) -> None:
    raw = _minimal_step("finish")
    raw["timeout_seconds"] = timeout_seconds
    with pytest.raises(ValidationError):
        FinishStep.model_validate(raw)


@pytest.mark.parametrize("timeout_seconds", [1, 120, 600])
def test_timeout_seconds_in_bounds_accepted(timeout_seconds: int) -> None:
    raw = _minimal_step("finish")
    raw["timeout_seconds"] = timeout_seconds
    step = FinishStep.model_validate(raw)
    assert step.timeout_seconds == timeout_seconds


@pytest.mark.parametrize("max_attempts", [0, -1, 6, 10])
def test_retry_policy_max_attempts_out_of_bounds_rejected(max_attempts: int) -> None:
    raw = _minimal_step("finish")
    raw["retry_policy"] = {"max_attempts": max_attempts}
    with pytest.raises(ValidationError):
        FinishStep.model_validate(raw)


@pytest.mark.parametrize("max_attempts", [1, 2, 5])
def test_retry_policy_max_attempts_in_bounds_accepted(max_attempts: int) -> None:
    raw = _minimal_step("finish")
    raw["retry_policy"] = {"max_attempts": max_attempts}
    step = FinishStep.model_validate(raw)
    assert step.retry_policy.max_attempts == max_attempts


@pytest.mark.parametrize("step_type", _STEP_TYPES)
def test_step_json_round_trip(step_type: StepType) -> None:
    original = STEP_MODELS[step_type].model_validate(_minimal_step(step_type))
    dumped = original.model_dump(mode="json")
    restored = _STEP_ADAPTER.validate_python(dumped)
    assert restored == original


def test_workflow_definition_json_round_trip_from_complete_definition() -> None:
    definition = WorkflowDefinition.model_validate(COMPLETE_DEFINITION)
    dumped = definition.model_dump(mode="json")
    restored = WorkflowDefinition.model_validate(dumped)
    assert restored == definition


def test_old_phase1_style_document_with_output_schema_still_parses() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Contract review",
        "steps": [
            {"id": "retrieve", "type": "retrieve_documents", "label": "Retrieve documents"},
            {"id": "finish", "type": "finish", "label": "Finish"},
        ],
        "edges": [{"source": "retrieve", "target": "finish"}],
        "tools": ["document_search"],
        "output_schema": {"type": "object"},
    }

    definition, _warnings = validate_definition(raw)

    assert isinstance(definition, WorkflowDefinition)
    assert not hasattr(definition, "output_schema")


def test_old_phase1_style_document_output_schema_is_dropped_not_stored() -> None:
    raw: dict[str, Any] = {
        "schema_version": 1,
        "name": "Minimal",
        "output_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }

    definition = WorkflowDefinition.model_validate(raw)

    assert "output_schema" not in definition.model_dump(mode="json")
