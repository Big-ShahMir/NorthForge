"""Smoke-test the hosted NVIDIA models configured for each routing role.

Run with a real ``NVIDIA_API_KEY`` configured (see ``docs/MODEL_ROUTING.md``)
to verify structured output, tool calls, embeddings, and reranking actually
work against the live API, and to discover which structured-output mode
and latency each model needs. Paces requests at 1.5 seconds apart to stay
under the hosted free tier's 40 requests/minute limit.

    python -m northforge.providers.smoke
    python -m northforge.providers.smoke --roles planner,extractor
    python -m northforge.providers.smoke --json

Never prints the API key: only role/model/outcome/error-code summaries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import cast

from pydantic import BaseModel

from northforge.core.config import Settings, get_settings
from northforge.providers.capabilities import CapabilityRegistry
from northforge.providers.errors import ProviderCapabilityError, ProviderError
from northforge.providers.nvidia import NvidiaProvider
from northforge.providers.types import (
    MODEL_ROLES,
    EmbeddingRequest,
    GenerationRequest,
    Message,
    ModelRole,
    RerankRequest,
    StructuredOutputMode,
    ToolDefinition,
)

_PING_PROMPT = "Reply with answer='pong' and number=7."
_TOOL_PROMPT = "Look up the refund policy."
_DRAFTER_PROMPT = "Say one short sentence about the weather."
_STRUCTURED_MODES: tuple[StructuredOutputMode, ...] = (
    "json_schema",
    "nvext_guided_json",
    "prompt_only",
)
_TOY_TOOL = ToolDefinition(
    name="lookup_policy",
    description="Look up a policy document by name.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The policy to look up."}},
        "required": ["name"],
    },
)

_ROLE_MODEL_SETTING: dict[ModelRole, str] = {
    "planner": "nvidia_model_planner",
    "extractor": "nvidia_model_extraction",
    "drafter": "nvidia_model_drafter",
    "evaluator": "nvidia_model_evaluator",
    "embedding": "embedding_model",
    "reranker": "reranker_model",
}


class Ping(BaseModel):
    """Tiny schema used to verify structured output end to end."""

    answer: str
    number: int


@dataclass
class SmokeResult:
    role: ModelRole
    model: str
    ok: bool
    structured_mode: StructuredOutputMode | None = None
    tool_call_ok: bool | None = None
    latency_ms: float | None = None
    request_id: str | None = None
    dimensions: int | None = None
    top_index: int | None = None
    error: str | None = None


def _error_code(error: Exception | None) -> str:
    if error is None:
        return "unknown_error"
    if isinstance(error, ProviderError):
        return error.code
    return type(error).__name__


def _resolve_model(settings: Settings, registry: CapabilityRegistry, role: ModelRole) -> str:
    override = getattr(settings, _ROLE_MODEL_SETTING[role])
    if override:
        return str(override)
    return registry.default_routes[role].primary


async def _run_generation_role(
    provider: NvidiaProvider,
    model: str,
    role: ModelRole,
    *,
    include_plain_generate: bool,
    pace_seconds: float,
) -> SmokeResult:
    result = SmokeResult(role=role, model=model, ok=False)
    last_error: Exception | None = None

    structured_request = GenerationRequest(
        messages=[Message(role="user", content=_PING_PROMPT)], metadata={"role": role}
    )
    structured_ok = False
    for mode in _STRUCTURED_MODES:
        try:
            response = await provider.generate_structured(
                structured_request, model, Ping, mode=mode
            )
        except ProviderCapabilityError as exc:
            last_error = exc
            break
        except ProviderError as exc:
            last_error = exc
            await asyncio.sleep(pace_seconds)
            continue
        result.structured_mode = mode
        result.latency_ms = response.latency_ms
        result.request_id = response.request_id
        structured_ok = True
        await asyncio.sleep(pace_seconds)
        break

    if not structured_ok:
        result.error = _error_code(last_error)
        return result

    tool_request = GenerationRequest(
        messages=[Message(role="user", content=_TOOL_PROMPT)],
        tool_choice="required",
        metadata={"role": role},
    )
    try:
        tool_response = await provider.tool_call(tool_request, model, [_TOY_TOOL])
        result.tool_call_ok = bool(tool_response.tool_calls)
    except ProviderError as exc:
        result.tool_call_ok = False
        result.error = _error_code(exc)
    await asyncio.sleep(pace_seconds)

    if include_plain_generate:
        try:
            plain_request = GenerationRequest(
                messages=[Message(role="user", content=_DRAFTER_PROMPT)], metadata={"role": role}
            )
            plain_response = await provider.generate(plain_request, model)
            result.request_id = result.request_id or plain_response.request_id
        except ProviderError as exc:
            result.error = _error_code(exc)
        await asyncio.sleep(pace_seconds)

    # ``structured_ok`` is always True here: the early return above handles the
    # only failure path for the structured-output check that gates ``ok``.
    result.ok = True
    return result


async def _run_embedding_role(
    provider: NvidiaProvider, model: str, pace_seconds: float
) -> SmokeResult:
    result = SmokeResult(role="embedding", model=model, ok=False)
    try:
        response = await provider.embed(
            EmbeddingRequest(texts=["north forge policy review", "contract clause extraction"]),
            model,
        )
    except ProviderError as exc:
        result.error = _error_code(exc)
        return result
    result.ok = True
    result.dimensions = response.dimensions
    result.latency_ms = response.latency_ms
    result.request_id = response.request_id
    await asyncio.sleep(pace_seconds)
    return result


async def _run_reranker_role(
    provider: NvidiaProvider, model: str, pace_seconds: float
) -> SmokeResult:
    result = SmokeResult(role="reranker", model=model, ok=False)
    try:
        response = await provider.rerank(
            RerankRequest(
                query="refund policy for damaged goods",
                passages=[
                    "Our refund policy covers damaged goods within 30 days.",
                    "Employees may take up to 15 days of paid leave per year.",
                    "Shipping delays are handled by the logistics team.",
                ],
            ),
            model,
        )
    except ProviderError as exc:
        result.error = _error_code(exc)
        return result
    result.ok = True
    result.top_index = response.rankings[0].index if response.rankings else None
    result.latency_ms = response.latency_ms
    result.request_id = response.request_id
    await asyncio.sleep(pace_seconds)
    return result


async def run_smoke(
    settings: Settings,
    registry: CapabilityRegistry,
    provider: NvidiaProvider,
    roles: Sequence[ModelRole],
    *,
    pace_seconds: float = 1.5,
) -> list[SmokeResult]:
    """Run the smoke sequence for each of ``roles`` and return one result per role."""
    results: list[SmokeResult] = []
    for role in roles:
        model = _resolve_model(settings, registry, role)
        if role == "embedding":
            results.append(await _run_embedding_role(provider, model, pace_seconds))
        elif role == "reranker":
            results.append(await _run_reranker_role(provider, model, pace_seconds))
        else:
            results.append(
                await _run_generation_role(
                    provider,
                    model,
                    role,
                    include_plain_generate=(role == "drafter"),
                    pace_seconds=pace_seconds,
                )
            )
    return results


def _cell(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


def _print_table(results: list[SmokeResult]) -> None:
    headers = [
        "role",
        "model",
        "ok",
        "structured_mode",
        "tool_call",
        "latency_ms",
        "request_id",
        "error",
    ]
    rows = [
        [
            result.role,
            result.model,
            _cell(result.ok),
            _cell(result.structured_mode),
            _cell(result.tool_call_ok),
            _cell(result.latency_ms),
            _cell(result.request_id),
            _cell(result.error),
        ]
        for result in results
    ]
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(headers)
    ]

    def render(cols: list[str]) -> str:
        return "  ".join(col.ljust(width) for col, width in zip(cols, widths, strict=True))

    print(render(headers))
    print(render(["-" * width for width in widths]))
    for row in rows:
        print(render(row))


def _print_markdown(results: list[SmokeResult]) -> None:
    print()
    print("| Role | Model | Verified | Structured mode | Tool calls | Latency (ms) |")
    print("|---|---|---|---|---|---|")
    for result in results:
        tool_call_cell = (
            "-" if result.tool_call_ok is None else ("yes" if result.tool_call_ok else "no")
        )
        print(
            f"| {result.role} | {result.model} | {'yes' if result.ok else 'no'} | "
            f"{_cell(result.structured_mode)} | {tool_call_cell} | {_cell(result.latency_ms)} |"
        )
    print()
    print(f"verified_on: {date.today().isoformat()}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test the hosted NVIDIA models for every routing role."
    )
    parser.add_argument(
        "--roles", type=str, default=None, help="Comma-separated roles to test (default: all)."
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of tables.")
    return parser.parse_args(argv)


async def _main_async(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    registry = CapabilityRegistry.load(settings.model_capabilities_file)
    roles: list[ModelRole]
    if args.roles:
        roles = cast("list[ModelRole]", [role.strip() for role in args.roles.split(",")])
    else:
        roles = list(MODEL_ROLES)
    provider = NvidiaProvider(settings, registry)
    try:
        results = await run_smoke(settings, registry, provider, roles)
    finally:
        await provider.aclose()

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        _print_table(results)
        _print_markdown(results)

    return 1 if any(not result.ok for result in results) else 0


def main() -> None:
    raise SystemExit(asyncio.run(_main_async()))


if __name__ == "__main__":
    main()


__all__ = ["SmokeResult", "main", "run_smoke"]
