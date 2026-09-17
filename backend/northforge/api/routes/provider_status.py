"""``GET /api/provider-status``: what the model router would do right now, without doing it.

The report is built from in-memory router state (routes, capability flags,
circuit states, last error per model) so it costs no provider quota and
stays truthful during an outage. It requires an authenticated caller
because it reveals configuration, and it never includes credentials or the
full provider URL.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from northforge.api.deps import get_model_router
from northforge.api.envelope import Envelope, request_id_of
from northforge.auth.dependencies import get_principal
from northforge.auth.principal import Principal
from northforge.providers.router import ModelRouter
from northforge.providers.status import ProviderStatus, build_provider_status

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/provider-status", response_model=Envelope[ProviderStatus])
async def provider_status(
    request: Request,
    _principal: Annotated[Principal, Depends(get_principal)],
    model_router: Annotated[ModelRouter, Depends(get_model_router)],
) -> Envelope[ProviderStatus]:
    return Envelope[ProviderStatus](
        data=build_provider_status(model_router, request.app.state.settings),
        request_id=request_id_of(request),
    )
