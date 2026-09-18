"""The enforcer: turns a model's ``PlannerProposal`` into a real ``WorkflowDefinition``.

Code decides what becomes a step; the model only proposes. ``compile_proposal``
applies exactly five rules, in order, and never trusts the model for anything
safety-relevant (registered tools, approval points, human supervision): see
the module-level docstring in ``planner/__init__.py`` and ``DECISIONS.md``
ADR-028. It never raises -- a proposal that cannot be parsed or does not
validate is returned as a ``CompileResult`` with ``definition=None`` or with
``errors`` populated, for the graph's repair turn (or the service layer,
which may still persist a semantically invalid draft) to handle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from northforge.core.errors import InvalidWorkflowError
from northforge.planner.schema import PlannerProposal, ProblemOut, RejectedAction
from northforge.schemas.workflow import WorkflowDefinition, parse_definition
from northforge.schemas.workflow_validation import validate_workflow

_COMMON_STEP_FIELDS = frozenset(
    {
        "id",
        "type",
        "label",
        "instructions",
        "timeout_seconds",
        "retry_policy",
        "requires_approval",
        "failure_policy",
    }
)

_UNSUPPORTED_TOOL_REASON = "Tool is not registered; only registered read-only tools may be used."
_REVIEW_ASSUMPTION = "A human review step was added before finish; NorthForge requires supervision."
_REVIEW_INSTRUCTIONS = "Added by NorthForge: a reviewer must approve before the workflow finishes."


@dataclass
class CompileResult:
    """The outcome of compiling one proposal: a definition, problems, and enforcer actions."""

    definition: WorkflowDefinition | None
    errors: list[ProblemOut] = field(default_factory=list)
    warnings: list[ProblemOut] = field(default_factory=list)
    rejected: list[RejectedAction] = field(default_factory=list)
    assumptions_added: list[str] = field(default_factory=list)


def _filter_tools(
    proposal_tools: list[str], known_tools: frozenset[str]
) -> tuple[list[str], list[RejectedAction]]:
    """Rule 1: keep only registered tools; record every dropped name."""
    kept: list[str] = []
    rejected: list[RejectedAction] = []
    for name in proposal_tools:
        if name in known_tools:
            if name not in kept:
                kept.append(name)
        else:
            rejected.append(
                RejectedAction(
                    action=name,
                    reason=_UNSUPPORTED_TOOL_REASON,
                    category="unsupported_tool",
                    source="enforcer",
                )
            )
    return kept, rejected


def _build_raw_steps(
    proposal: PlannerProposal,
) -> tuple[list[dict[str, Any]], list[ProblemOut]]:
    """Rule 2: flatten each proposed step into its raw dict, dropping stray common fields."""
    raw_steps: list[dict[str, Any]] = []
    warnings: list[ProblemOut] = []
    for index, step in enumerate(proposal.steps):
        config = dict(step.config)
        for key in sorted(_COMMON_STEP_FIELDS & config.keys()):
            config.pop(key)
            warnings.append(
                ProblemOut(
                    code="config_common_field_ignored",
                    message=(f"'{key}' is a common step field and was ignored inside 'config'"),
                    step_id=step.id or None,
                    path=f"steps/{index}/config/{key}",
                )
            )
        raw_steps.append(
            {
                "id": step.id,
                "type": step.type,
                "label": step.label,
                "instructions": step.instructions,
                "requires_approval": step.requires_approval,
                "failure_policy": step.failure_policy,
                **config,
            }
        )
    return raw_steps, warnings


def _compute_approval_points(raw_steps: list[dict[str, Any]]) -> list[str]:
    points = {step["id"] for step in raw_steps if step.get("requires_approval")}
    points |= {step["id"] for step in raw_steps if step.get("type") == "human_review"}
    return sorted(points)


def _build_inputs(
    proposal: PlannerProposal,
) -> tuple[dict[str, dict[str, Any]], list[ProblemOut]]:
    inputs: dict[str, dict[str, Any]] = {}
    warnings: list[ProblemOut] = []
    for index, proposed_input in enumerate(proposal.inputs):
        if not proposed_input.name:
            warnings.append(
                ProblemOut(
                    code="input_missing_name",
                    message="a proposed input had no name and was skipped",
                    path=f"inputs/{index}",
                )
            )
            continue
        inputs[proposed_input.name] = {
            "type": proposed_input.type,
            "description": proposed_input.description,
            "required": proposed_input.required,
        }
    return inputs, warnings


def _unique_step_id(preferred: str, existing_ids: set[str]) -> str:
    if preferred not in existing_ids:
        return preferred
    suffix = 2
    candidate = f"{preferred}_{suffix}"
    while candidate in existing_ids:
        suffix += 1
        candidate = f"{preferred}_{suffix}"
    return candidate


def _ensure_human_review(
    raw_steps: list[dict[str, Any]], raw_edges: list[dict[str, str]]
) -> list[str]:
    """Rule 3: insert a human-review step before ``finish`` when the model omitted one."""
    has_human_review = any(step.get("type") == "human_review" for step in raw_steps)
    finish_steps = [step for step in raw_steps if step.get("type") == "finish"]
    if has_human_review or not finish_steps:
        return []

    finish_step = finish_steps[0]
    finish_id = finish_step["id"]
    existing_ids = {step["id"] for step in raw_steps}
    review_id = _unique_step_id("review", existing_ids)

    result = finish_step.get("result")
    show = list(result.values()) if isinstance(result, dict) else []

    raw_steps.append(
        {
            "id": review_id,
            "type": "human_review",
            "label": "Human review",
            "instructions": _REVIEW_INSTRUCTIONS,
            "requires_approval": True,
            "failure_policy": "fail_run",
            "show": show,
        }
    )
    for edge in raw_edges:
        if edge["target"] == finish_id:
            edge["target"] = review_id
    raw_edges.append({"source": review_id, "target": finish_id})
    return [_REVIEW_ASSUMPTION]


def compile_proposal(
    proposal: PlannerProposal, *, known_tools: frozenset[str], user_request: str
) -> CompileResult:
    """Turn a model proposal into a ``WorkflowDefinition`` under fixed safety rules.

    Runs the pipeline regardless of ``proposal.outcome`` -- even a
    ``"rejected"`` proposal is compiled so the service layer has a
    consistent ``CompileResult`` to inspect; it is the caller's decision
    whether to persist the result.
    """
    warnings: list[ProblemOut] = []
    assumptions_added: list[str] = []

    tools, rejected = _filter_tools(proposal.tools, known_tools)

    raw_steps, config_warnings = _build_raw_steps(proposal)
    warnings.extend(config_warnings)

    raw_edges: list[dict[str, str]] = [
        {"source": edge.source, "target": edge.target} for edge in proposal.edges
    ]

    assumptions_added.extend(_ensure_human_review(raw_steps, raw_edges))

    inputs, input_warnings = _build_inputs(proposal)
    warnings.extend(input_warnings)

    raw: dict[str, Any] = {
        "name": proposal.name or "Planned workflow",
        "description": proposal.description,
        "user_request": user_request,
        "inputs": inputs,
        "steps": raw_steps,
        "edges": raw_edges,
        "tools": tools,
        "approval_points": _compute_approval_points(raw_steps),
        "policies": {},
    }

    try:
        definition = parse_definition(raw)
    except InvalidWorkflowError as exc:
        errors = [
            ProblemOut(
                code="parse_error",
                message=str(detail.get("msg", "")),
                step_id=None,
                path="/".join(str(part) for part in detail.get("loc", [])),
            )
            for detail in exc.details
        ]
        return CompileResult(
            definition=None,
            errors=errors,
            warnings=warnings,
            rejected=rejected,
            assumptions_added=assumptions_added,
        )

    report = validate_workflow(definition, known_tools=known_tools)
    errors = [
        ProblemOut(
            code=problem.code, message=problem.message, step_id=problem.step_id, path=problem.path
        )
        for problem in report.errors
    ]
    warnings.extend(
        ProblemOut(
            code=problem.code, message=problem.message, step_id=problem.step_id, path=problem.path
        )
        for problem in report.warnings
    )

    return CompileResult(
        definition=definition,
        errors=errors,
        warnings=warnings,
        rejected=rejected,
        assumptions_added=assumptions_added,
    )


__all__ = ["CompileResult", "compile_proposal"]
