"""Tool specifications: the typed, catalog-safe description of a tool.

A ``ToolSpec`` is everything the semantic validator and the (Stage B) tool
catalog endpoint need to know about a tool: its name, purpose, typed
argument and result contracts, and its safety classification. It never
carries the callable implementation itself -- that lives only in the
``ToolRegistry`` -- so a spec can be handed to API responses and validators
without risking exposure of tool internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

SideEffectClass = Literal["read_only", "draft_only"]
AccessScope = Literal["project_documents", "policy_library"]
ToolKind = Literal["retrieval", "lookup"]


@dataclass(frozen=True)
class ToolSpec:
    """Catalog-safe description of a registered tool.

    ``input_model`` and ``output_model`` are Pydantic models with
    ``extra="forbid"`` used to validate raw arguments and tool return values
    respectively; see ``northforge.tools.invoke.invoke_tool``.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    side_effect_class: SideEffectClass
    access_scope: AccessScope
    kind: ToolKind
