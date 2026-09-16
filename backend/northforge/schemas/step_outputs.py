"""Typed output models for each workflow step type.

See ``docs/WORKFLOW_SPEC.md`` and ``DECISIONS.md`` ADR-021 for the design
table this module implements exactly. Every model is ``extra="forbid"`` so a
step's recorded output is a stable, portable JSON shape that later steps can
reference by field name (``schemas/refs.py``, ``schemas/workflow_validation.py``).

``StepType`` lives here (rather than in ``schemas/steps.py``) so that both
``steps.py`` and this module can depend on it without an import cycle:
``steps.py`` needs ``StepType`` and ``OUTPUT_MODELS`` from here, and this
module has no need to import anything from ``steps.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import Citation, EvidenceChunk, Severity

StepType = Literal[
    "retrieve_documents",
    "extract_fields",
    "compare_policy",
    "classify",
    "draft_summary",
    "human_review",
    "validate_output",
    "finish",
]

FieldStatus = Literal["found", "missing", "conflicting"]
PolicyOutcome = Literal["pass", "fail", "exception", "unknown"]


class RetrieveDocumentsOutput(BaseModel):
    """Output of a ``retrieve_documents`` step."""

    model_config = ConfigDict(extra="forbid")

    chunks: list[EvidenceChunk] = Field(default_factory=list)


class ExtractedField(BaseModel):
    """A single extracted field value with its supporting evidence."""

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    evidence: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0
    status: FieldStatus = "missing"


class ExtractFieldsOutput(BaseModel):
    """Output of an ``extract_fields`` step: field name -> extracted value."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, ExtractedField] = Field(default_factory=dict)


class PolicyCheckResult(BaseModel):
    """The outcome of evaluating one policy rule against extracted fields."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    field: str = Field(min_length=1, max_length=200)
    outcome: PolicyOutcome = "unknown"
    severity: Severity = "medium"
    evidence: list[Citation] = Field(default_factory=list)
    explanation: str = ""


class ComparePolicyOutput(BaseModel):
    """Output of a ``compare_policy`` step."""

    model_config = ConfigDict(extra="forbid")

    results: list[PolicyCheckResult] = Field(default_factory=list)


class ClassifyOutput(BaseModel):
    """Output of a ``classify`` step."""

    model_config = ConfigDict(extra="forbid")

    category: str = ""
    confidence: float = 0.0
    evidence: list[Citation] = Field(default_factory=list)
    rationale: str = ""


class DraftSummaryOutput(BaseModel):
    """Output of a ``draft_summary`` step."""

    model_config = ConfigDict(extra="forbid")

    summary_markdown: str = ""
    citations: list[Citation] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class HumanReviewOutput(BaseModel):
    """Output of a ``human_review`` step."""

    model_config = ConfigDict(extra="forbid")

    decision: str = ""
    reviewer_notes: str = ""
    edited_payload: dict[str, Any] | None = None


class CheckOutcome(BaseModel):
    """One named check performed by a ``validate_output`` step."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool = False
    message: str = ""


class ValidateOutputOutput(BaseModel):
    """Output of a ``validate_output`` step."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = False
    checks: list[CheckOutcome] = Field(default_factory=list)


class FinishOutput(BaseModel):
    """Output of a ``finish`` step: the run's final, named result values."""

    model_config = ConfigDict(extra="forbid")

    result: dict[str, Any] = Field(default_factory=dict)


OUTPUT_MODELS: dict[StepType, type[BaseModel]] = {
    "retrieve_documents": RetrieveDocumentsOutput,
    "extract_fields": ExtractFieldsOutput,
    "compare_policy": ComparePolicyOutput,
    "classify": ClassifyOutput,
    "draft_summary": DraftSummaryOutput,
    "human_review": HumanReviewOutput,
    "validate_output": ValidateOutputOutput,
    "finish": FinishOutput,
}
