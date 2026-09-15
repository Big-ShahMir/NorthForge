"""Workflow definition schema: the stable envelope validated on every read and write.

See ``docs/WORKFLOW_SPEC.md``. Phase 2 defines the full typed per-step-type
field language; Phase 1 only validates the structural envelope: step ids are
unique, edges and approval points reference real steps, the step graph has
no cycle, and there is at most one ``finish`` step. ``validate_definition``
also reports soft warnings (missing ``finish``/``human_review`` steps,
unreachable steps, declared retrieval without tools) that callers may choose
to surface without blocking the write.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from northforge.core.errors import InvalidWorkflowError

STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

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


class WorkflowStep(BaseModel, extra="allow"):
    """A single workflow step. Phase 2 tightens the per-type field set."""

    id: str = Field(pattern=STEP_ID_PATTERN)
    type: StepType
    label: str = Field(min_length=1, max_length=200)
    requires_approval: bool = False


class WorkflowEdge(BaseModel, extra="forbid"):
    source: str
    target: str


class WorkflowDefinition(BaseModel, extra="forbid"):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    user_request: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStep] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    approval_points: list[str] = Field(default_factory=list)
    policies: dict[str, Any] = Field(default_factory=dict)

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


def _unreachable_steps(definition: WorkflowDefinition) -> list[str]:
    """Steps not reached by walking forward from a root.

    A root is a step with no incoming edge that participates in at least one
    edge (as a source or target). Steps untouched by any edge are excluded
    from the root set so they are reported as unreachable rather than
    trivially counted as their own root.
    """
    edge_nodes: set[str] = set()
    for edge in definition.edges:
        edge_nodes.add(edge.source)
        edge_nodes.add(edge.target)

    adjacency: dict[str, list[str]] = {step.id: [] for step in definition.steps}
    has_incoming: dict[str, bool] = {step.id: False for step in definition.steps}
    for edge in definition.edges:
        adjacency[edge.source].append(edge.target)
        has_incoming[edge.target] = True

    roots = [
        step_id
        for step_id, incoming in has_incoming.items()
        if not incoming and step_id in edge_nodes
    ]
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        stack.extend(adjacency[node])

    return sorted(set(has_incoming) - reachable)


def validate_definition(raw: dict[str, Any]) -> tuple[WorkflowDefinition, list[str]]:
    """Parse and structurally validate a workflow definition.

    Raises ``InvalidWorkflowError`` (hard failures: malformed shape, dangling
    references, cycles, multiple ``finish`` steps). Returns the parsed
    definition plus a list of soft warnings the caller may surface without
    blocking the write.
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

    warnings: list[str] = []
    if not definition.steps:
        warnings.append("workflow has no steps")
    if not any(step.type == "finish" for step in definition.steps):
        warnings.append("workflow has no 'finish' step")
    if not any(step.type == "human_review" for step in definition.steps):
        warnings.append("workflow has no 'human_review' step")
    if definition.edges:
        unreachable = _unreachable_steps(definition)
        if unreachable:
            warnings.append("step(s) not reachable from a root step: " + ", ".join(unreachable))
    if any(step.type == "retrieve_documents" for step in definition.steps) and not definition.tools:
        warnings.append("workflow has a 'retrieve_documents' step but no tools are declared")

    return definition, warnings
