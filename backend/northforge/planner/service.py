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

from northforge.db.models import Project, User, Workflow
from northforge.planner.schema import PlanJobResult
from northforge.providers.router import ModelRouter
from northforge.tools.registry import ToolRegistry


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
    raise NotImplementedError("implemented in Stage A (planner graph agent)")


__all__ = ["plan_workflow"]
