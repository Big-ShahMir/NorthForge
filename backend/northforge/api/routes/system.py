"""Liveness, readiness, and version endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from northforge import __version__
from northforge.api.envelope import Envelope, request_id_of
from northforge.core.health import CheckStatus, ReadinessProbe, ReadinessStatus

router = APIRouter(tags=["system"])


class HealthData(BaseModel):
    status: str
    version: str
    app_env: str


class CheckData(BaseModel):
    name: str
    status: CheckStatus
    latency_ms: float
    detail: str | None = None


class ReadinessData(BaseModel):
    status: ReadinessStatus
    checks: list[CheckData]


def get_readiness_probe(request: Request) -> ReadinessProbe:
    probe = getattr(request.app.state, "readiness_probe", None)
    if probe is None:
        raise RuntimeError("readiness probe not initialised; app lifespan did not run")
    assert isinstance(probe, ReadinessProbe)
    return probe


@router.get("/health", response_model=Envelope[HealthData])
async def health(request: Request) -> Envelope[HealthData]:
    return Envelope[HealthData](
        data=HealthData(
            status="ok", version=__version__, app_env=request.app.state.settings.app_env
        ),
        request_id=request_id_of(request),
    )


@router.get(
    "/ready",
    response_model=Envelope[ReadinessData],
    responses={503: {"model": Envelope[ReadinessData]}},
)
async def ready(
    request: Request, probe: Annotated[ReadinessProbe, Depends(get_readiness_probe)]
) -> JSONResponse:
    report = await probe.run()
    body = Envelope[ReadinessData](
        data=ReadinessData(
            status=report.status,
            checks=[CheckData(**check.__dict__) for check in report.checks],
        ),
        request_id=request_id_of(request),
    )
    return JSONResponse(status_code=report.http_status, content=body.model_dump(mode="json"))
