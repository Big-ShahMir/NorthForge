"""Plan a workflow for a project and persist the result as a draft version.

``plan_workflow`` is the single entry point the arq job (``worker/main.py``)
calls; tests call it directly with the shared test session. It gathers the
grounding facts, builds the caller's ``ToolContext`` (so probe tool calls are
access-filtered exactly like runtime calls), runs the compiled planner graph,
and stores the outcome through ``WorkflowsRepository``:

- ``workflow is None``: a new ``Workflow`` named from ``name`` or the
  proposal, with draft version 1.
- ``workflow`` given: a re-plan. The base version (``workflow.current_version``
  or the newest) supplies ``previous_request`` and its stored clarifying
  questions; the result becomes a new draft version and the current-version
  pointer is left unchanged.

``model_config_json`` receives ``model_router.snapshot()``,
``planner_output_json`` the ``PlannerOutput``, ``validation_warnings_json``
the semantic warnings as ``"<code>: <message>"`` strings, and
``source_request`` the requester's text (answers appended on a re-plan).
A rejected outcome, or a proposal from which no definition parsed, persists
nothing; the returned ``PlanJobResult`` then carries ``planner_output``.

``ProviderError`` propagates to the caller (the job maps it to a ``failed``
result with the stable code); nothing else is caught here.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from northforge.db.models import Project, User, Workflow, WorkflowVersion
from northforge.db.repositories.workflows import WorkflowsRepository
from northforge.planner.graph import PlannerDeps, run_planner
from northforge.planner.grounding import collect_grounding
from northforge.planner.schema import ClarifyingQuestion, PlanJobResult
from northforge.providers.router import ModelRouter
from northforge.tools.context import build_tool_context
from northforge.tools.registry import ToolRegistry


async def _resolve_base_version(
    repo: WorkflowsRepository, workflow: Workflow
) -> WorkflowVersion | None:
    if workflow.current_version is not None:
        return workflow.current_version
    return await repo.latest_version(workflow.id)


async def plan_workflow(
    session: AsyncSession,
    *,
    model_router: ModelRouter,
    tool_registry: ToolRegistry,
    project: Project,
    user: User,
    request: str | None,
    answers: Sequence[str] = (),
    name: str | None = None,
    workflow: Workflow | None = None,
) -> PlanJobResult:
    """Run the planner graph for ``request`` and persist a draft version (see module docstring).

    ``request`` may be ``None`` only on a re-plan (``workflow`` given), in which
    case the base version's ``source_request`` is reused and ``answers`` must
    be non-empty.
    """
    previous_request: str | None = None
    previous_questions: list[ClarifyingQuestion] = []
    base_version: WorkflowVersion | None = None
    repo = WorkflowsRepository(session)

    if workflow is not None:
        base_version = await _resolve_base_version(repo, workflow)
        if base_version is not None:
            previous_request = base_version.source_request
            raw_questions = base_version.planner_output_json.get("clarifying_questions", [])
            previous_questions = [
                ClarifyingQuestion.model_validate(raw_question) for raw_question in raw_questions
            ]

    request_text = request or previous_request
    if request_text is None:
        raise ValueError(
            "A request is required: no workflow was given, or it has no prior version to "
            "reuse a source request from."
        )

    grounding = await collect_grounding(
        session, project.id, frozenset(str(group) for group in user.access_groups_json)
    )
    tool_context = build_tool_context(user, str(project.id), tool_registry.names(), session)
    deps = PlannerDeps(
        model_router=model_router,
        tool_registry=tool_registry,
        tool_context=tool_context,
        grounding=grounding,
    )

    result = await run_planner(
        deps,
        request_text=request_text,
        answers=answers,
        previous_request=previous_request,
        previous_questions=previous_questions,
    )

    if not result.persistable:
        reason = (
            "nothing in the request could be turned into a supported workflow"
            if result.definition is None
            else "the request was rejected"
        )
        return PlanJobResult(
            outcome="rejected",
            planner_output=result.output,
            message=f"No workflow was created: {reason}.",
        )

    definition = result.definition
    assert definition is not None  # guaranteed by ``result.persistable`` above

    source_request = request_text
    if answers:
        answer_lines = "\n".join(f"- {answer}" for answer in answers)
        source_request = f"{request_text}\n\nAnswers:\n{answer_lines}"

    model_config_json = model_router.snapshot()
    planner_output_json = result.output.model_dump(mode="json")
    validation_warnings = [
        f"{problem.code}: {problem.message}" for problem in result.output.validation_warnings
    ]

    if workflow is None:
        workflow_name = name or result.proposal.name or definition.name
        created = await repo.create(
            project,
            workflow_name,
            definition.description,
            definition,
            user.id,
            source_request=source_request,
            model_config_json=model_config_json,
            planner_output_json=planner_output_json,
            validation_warnings=validation_warnings,
        )
        version = created.current_version
        assert version is not None
        return PlanJobResult(
            outcome=result.output.outcome, workflow_id=created.id, version_id=version.id
        )

    version = await repo.create_version(
        workflow,
        definition,
        user.id,
        source_request,
        model_config_json=model_config_json,
        planner_output_json=planner_output_json,
        validation_warnings=validation_warnings,
    )
    return PlanJobResult(
        outcome=result.output.outcome, workflow_id=workflow.id, version_id=version.id
    )


__all__ = ["plan_workflow"]
