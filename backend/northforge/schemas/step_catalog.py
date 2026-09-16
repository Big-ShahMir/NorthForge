"""The step type catalog: everything a planner or UI needs to build a step.

``config_schema`` is the *full* JSON Schema of the step's Pydantic model,
including the common fields defined on ``StepBase`` (id, type, label,
instructions, timeout_seconds, retry_policy, requires_approval,
failure_policy) rather than a schema of only the per-type fields. Diffing
the per-type model's schema against ``StepBase``'s to strip the common
fields would be fragile (shared ``$defs``, inherited field ordering) for no
real benefit: a caller building a step-editing UI needs the common fields'
constraints too. This is a deliberate deviation from the "config schema
minus common fields" alternative offered in the design.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from northforge.schemas.step_outputs import OUTPUT_MODELS, StepType
from northforge.schemas.steps import STEP_MODELS

#: Tool "kind" values (see ADR-022) a step of this type may use. Step types
#: absent here accept no tools at all.
_ALLOWED_TOOL_KINDS: dict[StepType, list[str]] = {
    "retrieve_documents": ["retrieval"],
    "compare_policy": ["lookup"],
}

_MODEL_DRIVEN: dict[StepType, bool] = {
    "retrieve_documents": False,
    "extract_fields": True,
    "compare_policy": False,
    "classify": True,
    "draft_summary": True,
    "human_review": False,
    "validate_output": False,
    "finish": False,
}

_TITLES: dict[StepType, str] = {
    "retrieve_documents": "Retrieve documents",
    "extract_fields": "Extract fields",
    "compare_policy": "Compare against policy",
    "classify": "Classify",
    "draft_summary": "Draft summary",
    "human_review": "Human review",
    "validate_output": "Validate output",
    "finish": "Finish",
}

_DESCRIPTIONS: dict[StepType, str] = {
    "retrieve_documents": "Retrieve permitted evidence using a query and filters.",
    "extract_fields": "Extract typed fields from selected evidence.",
    "compare_policy": (
        "Apply deterministic rules, and optionally model-assisted interpretation, "
        "to extracted fields."
    ),
    "classify": "Assign a typed category with supporting evidence.",
    "draft_summary": "Produce a structured draft with citations.",
    "human_review": "Pause and await a reviewer decision.",
    "validate_output": "Enforce schema, citations, required fields, and policy rules.",
    "finish": "Persist the final, named result values.",
}

_STEP_TYPE_ORDER: list[StepType] = [
    "retrieve_documents",
    "extract_fields",
    "compare_policy",
    "classify",
    "draft_summary",
    "human_review",
    "validate_output",
    "finish",
]


class StepTypeInfo(BaseModel):
    """A catalog entry describing one step type."""

    model_config = ConfigDict(extra="forbid")

    type: StepType
    title: str
    description: str
    config_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_tool_kinds: list[str]
    is_model_driven: bool


def step_catalog() -> list[StepTypeInfo]:
    """The full catalog of supported step types, in a stable order."""
    return [
        StepTypeInfo(
            type=step_type,
            title=_TITLES[step_type],
            description=_DESCRIPTIONS[step_type],
            config_schema=STEP_MODELS[step_type].model_json_schema(),
            output_schema=OUTPUT_MODELS[step_type].model_json_schema(),
            allowed_tool_kinds=_ALLOWED_TOOL_KINDS.get(step_type, []),
            is_model_driven=_MODEL_DRIVEN[step_type],
        )
        for step_type in _STEP_TYPE_ORDER
    ]
