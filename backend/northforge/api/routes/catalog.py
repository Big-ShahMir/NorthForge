"""Read-only catalog routes: registered tools and workflow step types.

Both endpoints expose static, process-wide catalogs -- there is nothing
project- or user-scoped about them -- but every route in this API requires
an authenticated caller, so these do too.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from northforge.api.envelope import Envelope, request_id_of
from northforge.auth.dependencies import get_current_user
from northforge.db.models import User
from northforge.schemas.step_catalog import StepTypeInfo, step_catalog
from northforge.tools.spec import AccessScope, SideEffectClass, ToolKind

router = APIRouter(prefix="/api", tags=["catalog"])

CurrentUser = Annotated[User, Depends(get_current_user)]


class ToolInfo(BaseModel):
    """Catalog-safe description of one registered tool: no implementation details."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    side_effect_class: SideEffectClass
    access_scope: AccessScope
    kind: ToolKind
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ToolInfo]


class StepTypeList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StepTypeInfo]


@router.get("/tools", response_model=Envelope[ToolList])
async def list_tools(request: Request, user: CurrentUser) -> Envelope[ToolList]:
    registry = request.app.state.tool_registry
    items = [
        ToolInfo(
            name=spec.name,
            description=spec.description,
            side_effect_class=spec.side_effect_class,
            access_scope=spec.access_scope,
            kind=spec.kind,
            input_schema=spec.input_model.model_json_schema(),
            output_schema=spec.output_model.model_json_schema(),
        )
        for spec in sorted(registry.specs(), key=lambda spec: spec.name)
    ]
    return Envelope[ToolList](data=ToolList(items=items), request_id=request_id_of(request))


@router.get("/workflow-step-types", response_model=Envelope[StepTypeList])
async def list_workflow_step_types(request: Request, user: CurrentUser) -> Envelope[StepTypeList]:
    return Envelope[StepTypeList](
        data=StepTypeList(items=step_catalog()), request_id=request_id_of(request)
    )
