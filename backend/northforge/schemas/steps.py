"""The typed workflow step language.

See ``docs/WORKFLOW_SPEC.md`` and ``DECISIONS.md`` ADR-020/ADR-021. Every
step type has a fixed set of configuration fields (all with defaults, so any
Phase 1 style step that only set the common fields still parses) plus the
common fields defined on ``StepBase``. Unknown fields are rejected
(``extra="forbid"``) -- the one piece of Phase 1 leniency Phase 2 removes.

Per-type configuration fields may hold either a literal value or a
``schemas.refs`` reference string; that distinction is not enforced by these
models (a reference is just a ``str``) but by the semantic validation layer
in ``schemas/workflow_validation.py``, which is the only place that needs to
know the reference syntax.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import Severity
from northforge.schemas.step_outputs import OUTPUT_MODELS, StepType

STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

FieldType = Literal["string", "text", "date", "number", "boolean"]
RuleOperator = Literal[
    "eq", "ne", "lt", "lte", "gt", "gte", "contains", "not_contains", "exists", "matches"
]
FailurePolicy = Literal["fail_run", "pause_for_review", "skip"]
ValidateCheck = Literal["schema", "required_fields", "citations", "policy_results"]


class RetryPolicy(BaseModel):
    """Bounded exponential backoff applied to a failed, retryable step."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=2, ge=1, le=5)
    backoff_seconds: float = Field(default=1.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)
    max_backoff_seconds: float = Field(default=30.0, ge=0)


class FieldSpec(BaseModel):
    """A single field an ``extract_fields`` step should populate."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=FIELD_NAME_PATTERN, max_length=100)
    description: str = ""
    type: FieldType = "string"
    required: bool = True


class DeterministicRule(BaseModel):
    """A single deterministic policy rule evaluated by ``compare_policy``."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    field: str = Field(min_length=1, max_length=200)
    operator: RuleOperator
    value: Any = None
    severity: Severity = "medium"
    description: str = ""


class StepBase(BaseModel):
    """Fields common to every step type."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=STEP_ID_PATTERN)
    type: StepType
    label: str = Field(min_length=1, max_length=200)
    instructions: str = ""
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    requires_approval: bool = False
    failure_policy: FailurePolicy = "fail_run"


class RetrieveDocumentsStep(StepBase):
    type: Literal["retrieve_documents"] = "retrieve_documents"
    tool: str = "search_documents"
    query: str = ""
    document_types: list[str] = Field(default_factory=list)
    vendor: str | None = None
    limit: int = Field(default=8, ge=1, le=20)
    min_results: int = 1


class ExtractFieldsStep(StepBase):
    type: Literal["extract_fields"] = "extract_fields"
    evidence: str = ""
    fields: list[FieldSpec] = Field(default_factory=list)
    require_citations: bool = True


class ComparePolicyStep(StepBase):
    type: Literal["compare_policy"] = "compare_policy"
    fields: str = ""
    policy_area: str = ""
    tool: str = "lookup_policy_rules"
    rules: list[DeterministicRule] = Field(default_factory=list)
    use_model_interpretation: bool = False


class ClassifyStep(StepBase):
    type: Literal["classify"] = "classify"
    evidence: str = ""
    categories: list[str] = Field(default_factory=list)


class DraftSummaryStep(StepBase):
    type: Literal["draft_summary"] = "draft_summary"
    sources: list[str] = Field(default_factory=list)
    require_citations: bool = True
    max_words: int = 400


class HumanReviewStep(StepBase):
    type: Literal["human_review"] = "human_review"
    show: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=lambda: ["approve", "reject", "edit"])


def _default_checks() -> list[ValidateCheck]:
    return ["schema", "required_fields", "citations"]


class ValidateOutputStep(StepBase):
    type: Literal["validate_output"] = "validate_output"
    target: str = ""
    checks: list[ValidateCheck] = Field(default_factory=_default_checks)


class FinishStep(StepBase):
    type: Literal["finish"] = "finish"
    result: dict[str, str] = Field(default_factory=dict)


WorkflowStep = Annotated[
    RetrieveDocumentsStep
    | ExtractFieldsStep
    | ComparePolicyStep
    | ClassifyStep
    | DraftSummaryStep
    | HumanReviewStep
    | ValidateOutputStep
    | FinishStep,
    Field(discriminator="type"),
]

STEP_MODELS: dict[StepType, type[StepBase]] = {
    "retrieve_documents": RetrieveDocumentsStep,
    "extract_fields": ExtractFieldsStep,
    "compare_policy": ComparePolicyStep,
    "classify": ClassifyStep,
    "draft_summary": DraftSummaryStep,
    "human_review": HumanReviewStep,
    "validate_output": ValidateOutputStep,
    "finish": FinishStep,
}


def step_output_fields(step_type: StepType) -> set[str]:
    """The top-level output field names produced by a step of this type."""
    return set(OUTPUT_MODELS[step_type].model_fields.keys())
