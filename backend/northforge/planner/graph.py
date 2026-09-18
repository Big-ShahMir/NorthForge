"""The planner's LangGraph state machine.

``screen -> ground -> probe_tools -> propose -> compile`` then, when the
enforcer found errors and this is the first attempt, one ``repair`` turn
and a second ``compile``; either way the graph ends at ``finalize``, which
builds the ``PlannerOutput`` persisted by ``service.py``. Every node is a
small async function closed over ``PlannerDeps`` so it can be unit-tested
directly (see ``tests/planner/test_graph.py``); ``ground`` does no SQL
itself -- grounding facts are gathered once by the caller (``service.py``)
and handed in through ``PlannerDeps.grounding``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from northforge.planner import prompts
from northforge.planner.compile import CompileResult, compile_proposal
from northforge.planner.grounding import ProbeOutcome, probe_tools
from northforge.planner.schema import (
    MAX_CLARIFYING_QUESTIONS,
    ClarifyingQuestion,
    GroundingSummary,
    PlannerOutput,
    PlannerProposal,
    PlanOutcome,
    PlanResult,
    RejectedAction,
    ToolProbeRecord,
)
from northforge.planner.screen import ScreenResult, screen_request
from northforge.providers.errors import ProviderError
from northforge.providers.router import ModelRouter, Route
from northforge.providers.types import (
    GenerationRequest,
    Message,
    ModelInvocationRecord,
    ModelResponse,
    Operation,
    Usage,
)
from northforge.tools.context import ToolContext
from northforge.tools.registry import ToolRegistry


@dataclass(frozen=True)
class PlannerDeps:
    """Everything a planner run needs beyond the request itself.

    ``grounding`` is gathered once by the caller (a single pair of SQL
    queries); the graph never queries the database directly.
    """

    model_router: ModelRouter
    tool_registry: ToolRegistry
    tool_context: ToolContext
    grounding: GroundingSummary
    probe_tools: bool = True


class PlannerState(TypedDict, total=False):
    """Accumulated state threaded through the graph's nodes."""

    request_text: str
    answers: Sequence[str]
    previous_request: str | None
    previous_questions: Sequence[ClarifyingQuestion]

    screen: ScreenResult
    tool_results: list[str]
    tool_probes: list[ToolProbeRecord]
    probe_invocations: list[ModelInvocationRecord]
    probe_warnings: list[str]

    propose_messages: list[Message]
    response_content: str | None
    proposal: PlannerProposal
    compile_result: CompileResult
    attempt: int
    repair_attempted: bool
    invocations: list[ModelInvocationRecord]

    result: PlanResult


def record_from_response(
    operation: Operation, response: ModelResponse[Any]
) -> ModelInvocationRecord:
    """Build a trace-safe invocation record from a successful routed response."""
    return ModelInvocationRecord(
        operation=operation,
        provider=response.provider,
        model=response.model,
        outcome="ok",
        latency_ms=response.latency_ms,
        role="planner",
        attempts=response.attempts,
        cache_hit=response.cache_hit,
        fallback_used=response.fallback_used,
        usage=response.usage,
        request_id=response.request_id,
    )


def record_from_error(
    operation: Operation, exc: ProviderError, route: Route
) -> ModelInvocationRecord:
    """Build a trace-safe invocation record from a caught ``ProviderError``."""
    return ModelInvocationRecord(
        operation=operation,
        provider=exc.provider or route.provider,
        model=exc.model or route.primary,
        outcome=exc.code,
        latency_ms=0.0,
        role="planner",
        attempts=int(getattr(exc, "attempts", 1)),
        cache_hit=False,
        fallback_used=False,
        usage=Usage(),
        request_id=exc.request_id,
    )


def _build_user_prompt(deps: PlannerDeps, state: PlannerState, *, with_tool_results: bool) -> str:
    screen_result = state["screen"]
    return prompts.build_user_prompt(
        request_text=screen_result.text,
        grounding=deps.grounding,
        excluded=screen_result.rejected,
        answers=state.get("answers", ()),
        previous_request=state.get("previous_request"),
        previous_questions=state.get("previous_questions", ()),
        tool_results=state.get("tool_results", ()) if with_tool_results else (),
    )


def build_planner_graph(
    deps: PlannerDeps,
) -> CompiledStateGraph[PlannerState, None, PlannerState, PlannerState]:
    """Compile the planner graph, with every node closed over ``deps``."""

    async def screen_node(state: PlannerState) -> dict[str, Any]:
        return {"screen": screen_request(state["request_text"])}

    async def ground_node(state: PlannerState) -> dict[str, Any]:
        # Grounding facts are gathered once by the caller (service.py) and
        # already live on ``deps.grounding``; this node exists only to keep
        # the graph's shape matching the design (screen -> ground -> ...).
        del state
        return {}

    async def probe_tools_node(state: PlannerState) -> dict[str, Any]:
        if not deps.probe_tools:
            return {
                "tool_results": [],
                "tool_probes": [],
                "probe_invocations": [],
                "probe_warnings": [],
            }
        system_prompt = prompts.build_system_prompt(deps.tool_registry.specs())
        user_prompt = _build_user_prompt(deps, state, with_tool_results=False)
        outcome: ProbeOutcome = await probe_tools(
            model_router=deps.model_router,
            tool_registry=deps.tool_registry,
            tool_context=deps.tool_context,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return {
            "tool_results": outcome.tool_results,
            "tool_probes": outcome.records,
            "probe_invocations": outcome.invocations,
            "probe_warnings": outcome.warnings,
        }

    async def propose_node(state: PlannerState) -> dict[str, Any]:
        system_prompt = prompts.build_system_prompt(deps.tool_registry.specs())
        user_prompt = _build_user_prompt(deps, state, with_tool_results=True)
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        request = GenerationRequest(
            messages=messages, temperature=0, max_tokens=4096, metadata={"stage": "propose"}
        )
        response = await deps.model_router.generate_structured("planner", request, PlannerProposal)
        proposal = response.parsed if response.parsed is not None else PlannerProposal()
        record = record_from_response("generate_structured", response)
        return {
            "propose_messages": messages,
            "proposal": proposal,
            "response_content": response.content,
            "invocations": [*state.get("invocations", []), record],
            "attempt": 0,
        }

    async def compile_node(state: PlannerState) -> dict[str, Any]:
        proposal = state["proposal"]
        screen_result = state["screen"]
        result = compile_proposal(
            proposal, known_tools=deps.tool_registry.names(), user_request=screen_result.text
        )
        return {"compile_result": result}

    async def repair_node(state: PlannerState) -> dict[str, Any]:
        compile_result = state["compile_result"]
        proposal = state["proposal"]
        previous_content = state.get("response_content") or proposal.model_dump_json()
        messages = [
            *state["propose_messages"],
            Message(role="assistant", content=previous_content),
            Message(role="user", content=prompts.build_repair_message(compile_result.errors)),
        ]
        request = GenerationRequest(
            messages=messages, temperature=0, max_tokens=4096, metadata={"stage": "repair"}
        )
        response = await deps.model_router.generate_structured("planner", request, PlannerProposal)
        repaired = response.parsed if response.parsed is not None else PlannerProposal()
        record = record_from_response("generate_structured", response)
        return {
            "proposal": repaired,
            "response_content": response.content,
            "invocations": [*state.get("invocations", []), record],
            "attempt": 1,
            "repair_attempted": True,
        }

    async def finalize_node(state: PlannerState) -> dict[str, Any]:
        screen_result = state["screen"]
        compile_result = state["compile_result"]
        proposal = state["proposal"]

        outcome: PlanOutcome = proposal.outcome
        if proposal.clarifying_questions and outcome == "proposed":
            outcome = "needs_clarification"

        assumptions = [*proposal.assumptions, *compile_result.assumptions_added]

        clarifying_questions = list(proposal.clarifying_questions)
        warnings: list[str] = [*state.get("probe_warnings", [])]
        if len(clarifying_questions) > MAX_CLARIFYING_QUESTIONS:
            clarifying_questions = clarifying_questions[:MAX_CLARIFYING_QUESTIONS]
            warnings.append(f"clarifying questions truncated to {MAX_CLARIFYING_QUESTIONS}")

        model_rejected = [
            RejectedAction(
                action=item.action, reason=item.reason, category=item.category, source="model"
            )
            for item in proposal.rejected_actions
        ]
        rejected_actions: list[RejectedAction] = []
        seen: set[tuple[str, str]] = set()
        for item in (*screen_result.rejected, *model_rejected, *compile_result.rejected):
            key = (item.category, item.action)
            if key in seen:
                continue
            seen.add(key)
            rejected_actions.append(item)

        invocations = [*state.get("probe_invocations", []), *state.get("invocations", [])]
        fallback_count = sum(1 for invocation in invocations if invocation.fallback_used)

        output = PlannerOutput(
            outcome=outcome,
            assumptions=assumptions,
            clarifying_questions=clarifying_questions,
            rejected_actions=rejected_actions,
            validation_errors=compile_result.errors,
            validation_warnings=compile_result.warnings,
            repair_attempted=state.get("repair_attempted", False),
            invocations=invocations,
            tool_probes=state.get("tool_probes", []),
            fallback_count=fallback_count,
            grounding=deps.grounding,
            warnings=warnings,
        )
        return {
            "result": PlanResult(
                definition=compile_result.definition, proposal=proposal, output=output
            )
        }

    def route_after_compile(state: PlannerState) -> str:
        compile_result = state["compile_result"]
        if state["proposal"].outcome == "rejected":
            # Nothing to repair: the model declined the request, and a
            # repair turn would only spend a call to rebuild an empty plan.
            return "finalize"
        if compile_result.errors and state.get("attempt", 0) == 0:
            return "repair"
        return "finalize"

    graph = StateGraph(PlannerState)
    graph.add_node("screen", screen_node)
    graph.add_node("ground", ground_node)
    graph.add_node("probe_tools", probe_tools_node)
    graph.add_node("propose", propose_node)
    graph.add_node("compile", compile_node)
    graph.add_node("repair", repair_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "screen")
    graph.add_edge("screen", "ground")
    graph.add_edge("ground", "probe_tools")
    graph.add_edge("probe_tools", "propose")
    graph.add_edge("propose", "compile")
    graph.add_conditional_edges(
        "compile", route_after_compile, {"repair": "repair", "finalize": "finalize"}
    )
    graph.add_edge("repair", "compile")
    graph.add_edge("finalize", END)

    return graph.compile()


async def run_planner(
    deps: PlannerDeps,
    *,
    request_text: str,
    answers: Sequence[str] = (),
    previous_request: str | None = None,
    previous_questions: Sequence[ClarifyingQuestion] = (),
) -> PlanResult:
    """Run the compiled planner graph once and return its ``PlanResult``."""
    graph = build_planner_graph(deps)
    initial_state: PlannerState = {
        "request_text": request_text,
        "answers": list(answers),
        "previous_request": previous_request,
        "previous_questions": list(previous_questions),
    }
    final_state = await graph.ainvoke(initial_state)
    result: PlanResult = final_state["result"]
    return result


__all__ = [
    "PlannerDeps",
    "PlannerState",
    "build_planner_graph",
    "record_from_error",
    "record_from_response",
    "run_planner",
]
