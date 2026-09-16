"""Workflow definition schema: the stable envelope validated on every read and write.

See ``docs/WORKFLOW_SPEC.md`` and ``DECISIONS.md`` ADR-020/ADR-021.

Two validation layers apply to every workflow document:

- The **parse layer** (this module, Pydantic): shape, enums, id patterns,
  unique step ids, edges referencing real steps, an acyclic step graph, and
  at most one ``finish`` step. Every per-type step field has a default, so
  any Phase 1 document that only used the envelope fields still parses.
  Unknown fields on a step are now rejected (``extra="forbid"``) -- the only
  Phase 1 leniency Phase 2 removes.
- The **semantic layer** (``schemas/workflow_validation.py``): required
  per-type configuration, reference wiring between steps, tool declarations,
  and approval-point bookkeeping. ``validate_definition`` delegates to it for
  warnings (with ``known_tools=None``, since this module must not depend on
  the tool registry); callers that need hard semantic errors -- the
  ``validate``/``approve`` endpoints -- call ``validate_workflow`` directly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from northforge.core.errors import InvalidWorkflowError
from northforge.schemas.steps import FinishStep, WorkflowStep
from northforge.schemas.workflow_validation import validate_workflow

WorkflowInputType = Literal["string", "text", "number", "boolean", "document_id", "list"]


class WorkflowInputSpec(BaseModel, extra="forbid"):
    """The declared shape of a single workflow input."""

    type: WorkflowInputType = "string"
    description: str = ""
    required: bool = True


class WorkflowEdge(BaseModel, extra="forbid"):
    source: str
    target: str


class WorkflowDefinition(BaseModel, extra="forbid"):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    user_request: str = ""
    inputs: dict[str, WorkflowInputSpec] = Field(default_factory=dict)
    steps: list[WorkflowStep] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    approval_points: list[str] = Field(default_factory=list)
    policies: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_output_schema(cls, data: Any) -> Any:
        """Ignore a leftover Phase 1 ``output_schema`` key instead of rejecting it.

        Phase 2 derives the output schema from the ``finish`` step
        (``derived_output_schema``) and no longer stores it, but old
        documents may still carry the key.
        """
        if isinstance(data, dict) and "output_schema" in data:
            data = dict(data)
            data.pop("output_schema", None)
        return data

    @model_validator(mode="after")
    def _validate_structure(self) -> WorkflowDefinition:
        step_ids = [step.id for step in self.steps]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for step_id in step_ids:
            if step_id in seen:
                duplicates.add(step_id)
            seen.add(step_id)
        if duplicates:
            raise ValueError(f"duplicate step id(s): {', '.join(sorted(duplicates))}")

        known_ids = set(step_ids)
        for edge in self.edges:
            if edge.source not in known_ids:
                raise ValueError(f"edge references unknown source step: {edge.source}")
            if edge.target not in known_ids:
                raise ValueError(f"edge references unknown target step: {edge.target}")

        for point in self.approval_points:
            if point not in known_ids:
                raise ValueError(f"approval point references unknown step: {point}")

        _ensure_acyclic(known_ids, self.edges)

        finish_steps = [step for step in self.steps if step.type == "finish"]
        if len(finish_steps) > 1:
            raise ValueError("at most one 'finish' step is allowed")

        return self


def _ensure_acyclic(step_ids: set[str], edges: list[WorkflowEdge]) -> None:
    """Kahn's algorithm: fewer visited nodes than step ids means a cycle exists."""
    in_degree: dict[str, int] = dict.fromkeys(step_ids, 0)
    adjacency: dict[str, list[str]] = {step_id: [] for step_id in step_ids}
    for edge in edges:
        adjacency[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    queue = [step_id for step_id, degree in in_degree.items() if degree == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(step_ids):
        raise ValueError("workflow edges contain a cycle")


def derived_output_schema(definition: WorkflowDefinition) -> dict[str, Any]:
    """Build a JSON Schema for the workflow's final result from its ``finish`` step.

    Property types cannot be inferred here: a ``finish`` step's ``result``
    maps output names to arbitrary step-output references, and this schema
    is meant only to declare which named outputs exist, not their types.
    """
    finish_steps = [step for step in definition.steps if isinstance(step, FinishStep)]
    if not finish_steps:
        return {"type": "object", "properties": {}, "required": []}

    keys = sorted(finish_steps[0].result.keys())
    return {
        "type": "object",
        "properties": {key: {} for key in keys},
        "required": keys,
    }


def validate_definition(raw: dict[str, Any]) -> tuple[WorkflowDefinition, list[str]]:
    """Parse and structurally validate a workflow definition.

    Raises ``InvalidWorkflowError`` for parse-layer hard failures (malformed
    shape, dangling references, cycles, multiple ``finish`` steps). Returns
    the parsed definition plus a list of soft warnings the caller may
    surface without blocking the write; these are the semantic layer's
    *warnings* only (``schemas/workflow_validation.validate_workflow``, run
    with ``known_tools=None``). Semantic *errors* -- missing required
    per-type config, bad references, undeclared tools, and the like -- are
    not raised here; callers that need to block on those call
    ``validate_workflow`` directly with the real tool registry.
    """
    try:
        definition = WorkflowDefinition.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        # Keep only JSON-serialisable fields; ``ctx`` may hold raw exception objects.
        details: list[Any] = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in errors
        ]
        raise InvalidWorkflowError(
            f"Workflow definition is invalid ({len(errors)} problem(s)).",
            details=details,
        ) from exc

    report = validate_workflow(definition, known_tools=None)
    warnings = [f"{problem.code}: {problem.message}" for problem in report.warnings]
    return definition, warnings
