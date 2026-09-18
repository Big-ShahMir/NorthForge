"""Live planner run against the hosted NVIDIA planner model.

Skipped unless ``NORTHFORGE_LIVE_MODELS=1`` and a real ``NVIDIA_API_KEY`` are
present (loaded from the repo ``.env``). One full planner pass costs two to
five requests on the free tier (probe round, propose, possibly a repair),
so this never runs in the default suite. Besides asserting the safety
invariants, it prints the models and fallback count so the D16 question
(whether Nemotron 3 Super is stable enough as the planner primary) can be
answered from evidence.
"""

from __future__ import annotations

import os

import pytest

from northforge.core.config import load_settings
from northforge.planner.graph import PlannerDeps, run_planner
from northforge.planner.schema import GroundingSummary
from northforge.providers.factory import build_model_router, close_model_router
from northforge.tools.registry import default_registry
from tests.planner.conftest import planner_tool_context
from tests.planner.fixtures import COMMON_REQUEST


def _has_nvidia_api_key() -> bool:
    try:
        return load_settings().nvidia_api_key is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("NORTHFORGE_LIVE_MODELS") != "1" or not _has_nvidia_api_key(),
    reason="set NORTHFORGE_LIVE_MODELS=1 and NVIDIA_API_KEY to run the live planner test",
)

#: What the synthetic corpus contains, so the live model plans against real names.
_GROUNDING = GroundingSummary(
    document_types={"msa": 17, "policy": 8, "sow": 7, "order_form": 6, "dpa": 5, "nda": 5},
    vendors={
        "Acme Cloud Services": 3,
        "Brightwater Facilities Group": 3,
        "Calderwood Data Services": 3,
        "Ferncastle Payments": 3,
        "Northwind Logistics": 3,
    },
    policy_areas={
        "renewal": 3,
        "data_protection": 2,
        "liability": 2,
        "payment": 1,
        "security": 1,
        "staffing": 1,
        "termination": 1,
    },
)


@pytest.mark.asyncio
async def test_live_planner_produces_a_supervised_workflow() -> None:
    settings = load_settings()
    router = build_model_router(settings)
    registry = default_registry()
    try:
        deps = PlannerDeps(
            model_router=router,
            tool_registry=registry,
            tool_context=planner_tool_context(),
            grounding=_GROUNDING,
            probe_tools=True,
        )
        result = await run_planner(deps, request_text=COMMON_REQUEST)
    finally:
        await close_model_router(router)

    output = result.output
    print(
        "\nlive planner:",
        f"outcome={output.outcome}",
        f"fallback_count={output.fallback_count}",
        f"repair_attempted={output.repair_attempted}",
        f"errors={[problem.code for problem in output.validation_errors]}",
        f"invocations={[(r.operation, r.model, r.outcome) for r in output.invocations]}",
        f"tool_probes={[(r.name, r.outcome) for r in output.tool_probes]}",
    )

    assert output.outcome in {"proposed", "needs_clarification"}
    assert result.definition is not None, output.validation_errors
    step_types = [step.type for step in result.definition.steps]
    assert "finish" in step_types
    assert "human_review" in step_types
    assert set(result.definition.tools) <= registry.names()
    assert all(
        getattr(step, "tool", None) in (None, *registry.names()) for step in result.definition.steps
    )
