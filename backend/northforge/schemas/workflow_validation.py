"""Semantic validation of a parsed workflow definition (ADR-021).

This is the second of two validation layers. The parse layer
(``schemas/workflow.py``, Pydantic) only checks shape: enums, id patterns,
unique ids, edges referencing real steps, an acyclic step graph, and at most
one ``finish`` step. Everything else -- required per-type configuration,
reference wiring between steps, tool declarations, and approval-point
bookkeeping -- is checked here, against an already-parsed
``WorkflowDefinition``.

``validate_workflow`` never raises; it always returns a ``ValidationReport``.
Callers decide what to do with hard errors (block a validate/approve
request) versus warnings (store them, but let the draft save).
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from northforge.schemas.refs import parse_ref
from northforge.schemas.step_outputs import StepType
from northforge.schemas.steps import (
    ClassifyStep,
    ComparePolicyStep,
    DraftSummaryStep,
    ExtractFieldsStep,
    FinishStep,
    HumanReviewStep,
    RetrieveDocumentsStep,
    ValidateOutputStep,
    WorkflowStep,
    step_output_fields,
)

if TYPE_CHECKING:
    from northforge.schemas.workflow import WorkflowDefinition, WorkflowEdge

#: The only tools a step of this type is permitted to name. Step types absent
#: from this mapping have no ``tool`` configuration field at all, so they can
#: never name a tool -- structurally enforcing "steps of other types must not
#: name tools".
ALLOWED_TOOLS_BY_STEP: dict[StepType, set[str]] = {
    "retrieve_documents": {"search_documents", "get_document_chunk"},
    "compare_policy": {"lookup_policy_rules"},
}

_MAX_RETRY_BUDGET_SECONDS = 20 * 60


@dataclass(frozen=True, slots=True)
class Problem:
    """A single semantic validation finding."""

    code: str
    message: str
    step_id: str | None = None
    path: str = ""


@dataclass(slots=True)
class ValidationReport:
    """The full result of semantically validating a workflow definition."""

    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_workflow(
    definition: WorkflowDefinition, *, known_tools: AbstractSet[str] | None
) -> ValidationReport:
    """Run every semantic check against a parsed workflow definition.

    ``known_tools`` is the set of tool names actually registered at runtime
    (a ``set`` or ``frozenset``, e.g. ``ToolRegistry.names()``). Passing
    ``None`` skips the ``tool_not_registered`` check entirely (used by
    ``validate_definition``, which must not depend on the tool registry).
    """
    errors: list[Problem] = []
    warnings: list[Problem] = []

    steps_by_id: dict[str, WorkflowStep] = {step.id: step for step in definition.steps}
    index_by_id: dict[str, int] = {step.id: index for index, step in enumerate(definition.steps)}
    ancestors = _compute_ancestors(set(steps_by_id), definition.edges)

    finish_steps = [step for step in definition.steps if isinstance(step, FinishStep)]
    if not finish_steps:
        errors.append(
            Problem(code="no_finish_step", message="workflow has no 'finish' step", path="steps")
        )

    if not any(isinstance(step, HumanReviewStep) for step in definition.steps):
        warnings.append(
            Problem(
                code="no_human_review",
                message="workflow has no 'human_review' step",
                path="steps",
            )
        )

    if definition.edges:
        for step_id in _unreachable_steps(definition):
            errors.append(
                Problem(
                    code="unreachable_step",
                    message=f"step '{step_id}' is not reachable from a root step",
                    step_id=step_id,
                    path=f"steps/{index_by_id[step_id]}",
                )
            )

    if known_tools is not None:
        for tool_index, tool_name in enumerate(definition.tools):
            if tool_name not in known_tools:
                errors.append(
                    Problem(
                        code="tool_not_registered",
                        message=f"tool '{tool_name}' is not registered",
                        path=f"tools/{tool_index}",
                    )
                )

    expected_approval_points: set[str] = {
        step.id for step in definition.steps if step.requires_approval
    } | {step.id for step in definition.steps if isinstance(step, HumanReviewStep)}
    actual_approval_points = set(definition.approval_points)
    if expected_approval_points != actual_approval_points:
        missing = sorted(expected_approval_points - actual_approval_points)
        extra = sorted(actual_approval_points - expected_approval_points)
        detail_parts = []
        if missing:
            detail_parts.append(f"missing: {', '.join(missing)}")
        if extra:
            detail_parts.append(f"unexpected: {', '.join(extra)}")
        errors.append(
            Problem(
                code="approval_point_mismatch",
                message=(
                    "approval_points must equal the steps with requires_approval=True "
                    "plus every human_review step (" + "; ".join(detail_parts) + ")"
                ),
                path="approval_points",
            )
        )

    for index, step in enumerate(definition.steps):
        base_path = f"steps/{index}"
        step_ancestors = ancestors.get(step.id, set())

        if isinstance(step, RetrieveDocumentsStep):
            if not step.query:
                errors.append(_missing_config(step.id, base_path, "query", "retrieve_documents"))
            _check_ref(
                step.query,
                step_id=step.id,
                path=f"{base_path}/query",
                step_ancestors=step_ancestors,
                steps_by_id=steps_by_id,
                inputs=definition.inputs,
                errors=errors,
            )
            _check_tool(step, definition.tools, known_tools, errors, base_path)

        elif isinstance(step, ExtractFieldsStep):
            if not step.fields:
                errors.append(_missing_config(step.id, base_path, "fields", "extract_fields"))
            if not step.evidence:
                errors.append(_missing_config(step.id, base_path, "evidence", "extract_fields"))
            _check_ref(
                step.evidence,
                step_id=step.id,
                path=f"{base_path}/evidence",
                step_ancestors=step_ancestors,
                steps_by_id=steps_by_id,
                inputs=definition.inputs,
                errors=errors,
            )
            if not any(
                isinstance(steps_by_id.get(ancestor), RetrieveDocumentsStep)
                for ancestor in step_ancestors
            ):
                warnings.append(
                    Problem(
                        code="no_retrieval_before_extraction",
                        message=(
                            f"extract_fields step '{step.id}' has no retrieve_documents ancestor"
                        ),
                        step_id=step.id,
                        path=base_path,
                    )
                )

        elif isinstance(step, ComparePolicyStep):
            if not step.fields:
                errors.append(_missing_config(step.id, base_path, "fields", "compare_policy"))
            if not step.policy_area and not step.rules:
                errors.append(
                    Problem(
                        code="missing_required_config",
                        message=(
                            "compare_policy requires 'policy_area' or a non-empty 'rules' list"
                        ),
                        step_id=step.id,
                        path=f"{base_path}/policy_area",
                    )
                )
            _check_ref(
                step.fields,
                step_id=step.id,
                path=f"{base_path}/fields",
                step_ancestors=step_ancestors,
                steps_by_id=steps_by_id,
                inputs=definition.inputs,
                errors=errors,
            )
            _check_tool(step, definition.tools, known_tools, errors, base_path)

        elif isinstance(step, ClassifyStep):
            if not step.evidence:
                errors.append(_missing_config(step.id, base_path, "evidence", "classify"))
            if len(step.categories) < 2:
                errors.append(
                    Problem(
                        code="missing_required_config",
                        message="classify requires at least two 'categories'",
                        step_id=step.id,
                        path=f"{base_path}/categories",
                    )
                )
            _check_ref(
                step.evidence,
                step_id=step.id,
                path=f"{base_path}/evidence",
                step_ancestors=step_ancestors,
                steps_by_id=steps_by_id,
                inputs=definition.inputs,
                errors=errors,
            )

        elif isinstance(step, DraftSummaryStep):
            if not step.sources:
                errors.append(
                    Problem(
                        code="missing_required_config",
                        message="draft_summary requires at least one 'sources' entry",
                        step_id=step.id,
                        path=f"{base_path}/sources",
                    )
                )
            for source_index, source in enumerate(step.sources):
                _check_ref(
                    source,
                    step_id=step.id,
                    path=f"{base_path}/sources/{source_index}",
                    step_ancestors=step_ancestors,
                    steps_by_id=steps_by_id,
                    inputs=definition.inputs,
                    errors=errors,
                )
            if not step.require_citations:
                warnings.append(
                    Problem(
                        code="draft_without_citations",
                        message=f"draft_summary step '{step.id}' does not require citations",
                        step_id=step.id,
                        path=f"{base_path}/require_citations",
                    )
                )

        elif isinstance(step, HumanReviewStep):
            for show_index, shown in enumerate(step.show):
                _check_ref(
                    shown,
                    step_id=step.id,
                    path=f"{base_path}/show/{show_index}",
                    step_ancestors=step_ancestors,
                    steps_by_id=steps_by_id,
                    inputs=definition.inputs,
                    errors=errors,
                )

        elif isinstance(step, ValidateOutputStep):
            if not step.target:
                errors.append(_missing_config(step.id, base_path, "target", "validate_output"))
            _check_ref(
                step.target,
                step_id=step.id,
                path=f"{base_path}/target",
                step_ancestors=step_ancestors,
                steps_by_id=steps_by_id,
                inputs=definition.inputs,
                errors=errors,
            )

        elif isinstance(step, FinishStep):
            if not step.result:
                errors.append(_missing_config(step.id, base_path, "result", "finish"))
            for key, value in step.result.items():
                _check_ref(
                    value,
                    step_id=step.id,
                    path=f"{base_path}/result/{key}",
                    step_ancestors=step_ancestors,
                    steps_by_id=steps_by_id,
                    inputs=definition.inputs,
                    errors=errors,
                    require_ref=True,
                )

        retry_budget = step.retry_policy.max_attempts * step.timeout_seconds
        if retry_budget > _MAX_RETRY_BUDGET_SECONDS:
            warnings.append(
                Problem(
                    code="high_retry_budget",
                    message=(
                        f"retry_policy.max_attempts x timeout_seconds = {retry_budget}s "
                        "exceeds 20 minutes"
                    ),
                    step_id=step.id,
                    path=f"{base_path}/retry_policy",
                )
            )

    return ValidationReport(errors=errors, warnings=warnings)


def _missing_config(step_id: str, base_path: str, field_name: str, step_type: str) -> Problem:
    return Problem(
        code="missing_required_config",
        message=f"{step_type} requires '{field_name}'",
        step_id=step_id,
        path=f"{base_path}/{field_name}",
    )


def _check_tool(
    step: RetrieveDocumentsStep | ComparePolicyStep,
    declared_tools: list[str],
    known_tools: AbstractSet[str] | None,
    errors: list[Problem],
    base_path: str,
) -> None:
    path = f"{base_path}/tool"
    if step.tool not in declared_tools:
        errors.append(
            Problem(
                code="tool_not_declared",
                message=(
                    f"step '{step.id}' uses tool '{step.tool}' which is not declared "
                    "in workflow tools"
                ),
                step_id=step.id,
                path=path,
            )
        )
    allowed = ALLOWED_TOOLS_BY_STEP.get(step.type, set())
    if step.tool not in allowed:
        errors.append(
            Problem(
                code="tool_not_allowed_for_step",
                message=f"tool '{step.tool}' is not allowed for step type '{step.type}'",
                step_id=step.id,
                path=path,
            )
        )
    if known_tools is not None and step.tool not in known_tools:
        errors.append(
            Problem(
                code="tool_not_registered",
                message=f"tool '{step.tool}' is not registered",
                step_id=step.id,
                path=path,
            )
        )


def _check_ref(
    value: str,
    *,
    step_id: str,
    path: str,
    step_ancestors: set[str],
    steps_by_id: dict[str, WorkflowStep],
    inputs: dict[str, Any],
    errors: list[Problem],
    require_ref: bool = False,
) -> None:
    """Validate a single configuration value that may hold a reference.

    ``require_ref=True`` is used for ``finish.result`` values, which must be
    references (a literal there can never be correct: the whole point of
    ``finish`` is to gather other steps' outputs). For every other ref-typed
    field, a literal is valid and simply skipped.
    """
    try:
        ref = parse_ref(value)
    except ValueError as exc:
        code = "finish_result_reference_invalid" if require_ref else "invalid_reference_syntax"
        errors.append(Problem(code=code, message=str(exc), step_id=step_id, path=path))
        return

    if ref is None:
        if require_ref:
            errors.append(
                Problem(
                    code="finish_result_reference_invalid",
                    message=(
                        f"finish result value {value!r} must be a reference such as "
                        "'$step.<id>.<field>'"
                    ),
                    step_id=step_id,
                    path=path,
                )
            )
        return

    if ref.kind == "input":
        if ref.name not in inputs:
            errors.append(
                Problem(
                    code="unknown_input_reference",
                    message=f"unknown workflow input '{ref.name}'",
                    step_id=step_id,
                    path=path,
                )
            )
        return

    target = steps_by_id.get(ref.name)
    if target is None or ref.name not in step_ancestors:
        errors.append(
            Problem(
                code="reference_not_ancestor",
                message=f"'{ref.name}' is not an ancestor step of '{step_id}'",
                step_id=step_id,
                path=path,
            )
        )
        return

    if ref.field is None or ref.field not in step_output_fields(target.type):
        errors.append(
            Problem(
                code="reference_unknown_field",
                message=f"step '{ref.name}' has no output field '{ref.field}'",
                step_id=step_id,
                path=path,
            )
        )


def _compute_ancestors(step_ids: set[str], edges: list[WorkflowEdge]) -> dict[str, set[str]]:
    predecessors: dict[str, list[str]] = {step_id: [] for step_id in step_ids}
    for edge in edges:
        predecessors.setdefault(edge.target, []).append(edge.source)

    ancestors: dict[str, set[str]] = {}
    for step_id in step_ids:
        seen: set[str] = set()
        stack = list(predecessors.get(step_id, []))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(predecessors.get(node, []))
        ancestors[step_id] = seen
    return ancestors


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
