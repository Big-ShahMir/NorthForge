"""Planner contracts: what the model proposes, what the enforcer records, what is persisted.

Three audiences share this module:

- ``PlannerProposal`` is the structured-output schema handed to the model. It
  is deliberately *flat*: a step is ``{id, type, label, ..., config}`` with the
  per-type fields inside ``config`` rather than the eight-way discriminated
  union of ``schemas/steps.py``, which is too large for hosted ``json_schema``
  mode. ``compile.py`` turns it into a real ``WorkflowDefinition`` and the
  parse layer rejects anything malformed. Every field has a default so the
  mock provider's fallback response (``schema.model_validate({})``) is valid.
- ``PlannerOutput`` is what ``workflow_versions.planner_output_json`` stores
  and ``GET /api/workflow-versions/{id}`` returns: assumptions, clarifying
  questions, rejected actions, remaining validation problems, and trace-safe
  model and tool records. It never carries prompt text or credentials.
- ``PlanJobResult`` is the arq job's return value (``GET /api/jobs/{id}``).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.providers.types import ModelInvocationRecord
from northforge.schemas.step_outputs import StepType
from northforge.schemas.steps import FailurePolicy
from northforge.schemas.workflow import WorkflowDefinition, WorkflowInputType

PLANNER_VERSION = "1"
"""Bump when the prompt or enforcer rules change in a way that alters proposals."""

MAX_REQUEST_CHARS = 4000
MAX_ANSWERS = 10
MAX_ANSWER_CHARS = 1000
MAX_CLARIFYING_QUESTIONS = 3
MAX_TOOL_PROBE_ROUNDS = 2
MAX_TOOL_PROBE_CALLS = 4
MAX_TOOL_RESULT_CHARS = 1500

PlanOutcome = Literal["proposed", "needs_clarification", "rejected"]
JobOutcome = Literal["proposed", "needs_clarification", "rejected", "failed"]
RejectionCategory = Literal[
    "side_effect",
    "unsupported_tool",
    "unsupported_step",
    "out_of_scope",
    "prompt_injection",
    "policy_override",
]
RejectionSource = Literal["screen", "model", "enforcer"]


# -- model-facing proposal ------------------------------------------------------


class ProposedInput(BaseModel):
    """A workflow input the user must supply when running the workflow."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=100, description="snake_case input name")
    type: WorkflowInputType = "string"
    description: str = ""
    required: bool = True


class ProposedStep(BaseModel):
    """One step. ``config`` holds only the per-type fields listed in the system prompt."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=64, description="snake_case, unique within the workflow")
    type: StepType = "retrieve_documents"
    label: str = Field(default="", max_length=200)
    instructions: str = Field(
        default="", description="Plain-language guidance for the person or model running the step"
    )
    requires_approval: bool = False
    failure_policy: FailurePolicy = "fail_run"
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-type configuration fields only (see the step table); no common fields",
    )


class ProposedEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = ""
    target: str = ""


class ClarifyingQuestion(BaseModel):
    """A question the planner needs answered; ``default_assumption`` is what it assumed for now."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=32, description="short id such as q1")
    question: str = ""
    why_it_matters: str = ""
    default_assumption: str | None = None


class RejectedAction(BaseModel):
    """Something the request asked for that will not become a step."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(default="", description="What was asked, in the requester's words")
    reason: str = ""
    category: RejectionCategory = "out_of_scope"
    source: RejectionSource = "model"


class PlannerProposal(BaseModel):
    """The model's answer. Everything here is advisory until ``compile.py`` accepts it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=200, description="Short workflow name")
    description: str = ""
    outcome: PlanOutcome = Field(
        default="proposed",
        description=(
            "'proposed' when the request is clear; 'needs_clarification' when questions remain "
            "but a best-effort workflow is still given; 'rejected' only when nothing in the "
            "request can be done with the supported steps and tools"
        ),
    )
    inputs: list[ProposedInput] = Field(default_factory=list)
    steps: list[ProposedStep] = Field(default_factory=list)
    edges: list[ProposedEdge] = Field(default_factory=list)
    tools: list[str] = Field(
        default_factory=list, description="Every tool any step uses; registered names only"
    )
    assumptions: list[str] = Field(default_factory=list)
    clarifying_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    rejected_actions: list[RejectedAction] = Field(default_factory=list)


# -- enforcer and persisted output ---------------------------------------------------


class ProblemOut(BaseModel):
    """JSON form of ``schemas.workflow_validation.Problem``."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    step_id: str | None = None
    path: str = ""


class ToolProbeRecord(BaseModel):
    """Trace-safe record of one grounding tool call (mirrors ``tools.context.ToolCallRecord``)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    outcome: str
    duration_ms: float = Field(ge=0.0)


class GroundingSummary(BaseModel):
    """Facts about the project the planner was shown: value -> count, access-filtered."""

    model_config = ConfigDict(extra="forbid")

    document_types: dict[str, int] = Field(default_factory=dict)
    vendors: dict[str, int] = Field(default_factory=dict)
    policy_areas: dict[str, int] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """Persisted as ``workflow_versions.planner_output_json``; safe to return from the API."""

    model_config = ConfigDict(extra="forbid")

    planner_version: str = PLANNER_VERSION
    outcome: PlanOutcome = "proposed"
    assumptions: list[str] = Field(default_factory=list)
    clarifying_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    rejected_actions: list[RejectedAction] = Field(default_factory=list)
    validation_errors: list[ProblemOut] = Field(
        default_factory=list, description="Semantic errors still present after the repair turn"
    )
    validation_warnings: list[ProblemOut] = Field(default_factory=list)
    repair_attempted: bool = False
    invocations: list[ModelInvocationRecord] = Field(default_factory=list)
    tool_probes: list[ToolProbeRecord] = Field(default_factory=list)
    fallback_count: int = Field(default=0, ge=0)
    grounding: GroundingSummary = Field(default_factory=GroundingSummary)
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal planner notices (probe failures, truncation, defaults applied)",
    )


class PlanResult(BaseModel):
    """What the graph returns to ``service.py``. ``definition`` is ``None`` when nothing parsed."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    definition: WorkflowDefinition | None = None
    proposal: PlannerProposal
    output: PlannerOutput

    @property
    def persistable(self) -> bool:
        """Whether a draft should be stored.

        Not when the request was rejected, nothing parsed, or there is no
        ``finish`` step: a definition without one is not a workflow at all
        (the model produced no usable steps), so it is reported, not saved.
        """
        if self.output.outcome == "rejected" or self.definition is None:
            return False
        return any(step.type == "finish" for step in self.definition.steps)


class PlanJobResult(BaseModel):
    """Return value of the ``plan_workflow`` arq job, surfaced by ``GET /api/jobs/{id}``.

    ``planner_output`` is filled only when no version was created (``rejected``)
    so the caller can still read the reasons; otherwise it lives on the version.
    """

    model_config = ConfigDict(extra="forbid")

    outcome: JobOutcome
    workflow_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    error_code: str | None = None
    message: str | None = None
    planner_output: PlannerOutput | None = None


__all__ = [
    "MAX_ANSWERS",
    "MAX_ANSWER_CHARS",
    "MAX_CLARIFYING_QUESTIONS",
    "MAX_REQUEST_CHARS",
    "MAX_TOOL_PROBE_CALLS",
    "MAX_TOOL_PROBE_ROUNDS",
    "MAX_TOOL_RESULT_CHARS",
    "PLANNER_VERSION",
    "ClarifyingQuestion",
    "GroundingSummary",
    "JobOutcome",
    "PlanJobResult",
    "PlanOutcome",
    "PlanResult",
    "PlannerOutput",
    "PlannerProposal",
    "ProblemOut",
    "ProposedEdge",
    "ProposedInput",
    "ProposedStep",
    "RejectedAction",
    "RejectionCategory",
    "RejectionSource",
    "ToolProbeRecord",
]
