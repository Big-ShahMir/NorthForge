"""Model capability registry loaded from ``model_catalog.json``.

Model names and what each model can do live in configuration (the JSON
catalog, overridable with ``MODEL_CAPABILITIES_FILE``), never in
application code. The router consults the registry before selecting a
model for a role, and ``NvidiaProvider`` consults it to decide how to
request structured output or toggle reasoning for a given model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from northforge.core.errors import ConfigurationError
from northforge.providers.types import (
    MODEL_ROLES,
    Modality,
    ModelRole,
    StructuredOutputMode,
)

DEFAULT_CATALOG_PATH = Path(__file__).with_name("model_catalog.json")

ProviderName = Literal["nvidia", "mock"]


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)
    provider: ProviderName
    modality: Modality
    supports_structured_output: bool = False
    structured_output_mode: StructuredOutputMode | None = None
    supports_tools: bool = False
    supports_reasoning_toggle: bool = False
    context_window: int = Field(ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    embedding_dimensions: int | None = Field(default=None, ge=1)
    expected_latency_ms: int | None = Field(default=None, ge=0)
    # Applied when a request leaves ``reasoning`` unset and the model has the
    # toggle: hosted Nemotron models return HTTP 500 or empty tool calls with
    # thinking on, so their default is off.
    default_reasoning: bool | None = None
    # Per-model read timeout; overrides MODEL_REQUEST_TIMEOUT_SECONDS when the
    # request itself sets none. Hosted queueing for the largest models runs to
    # two minutes even for one-sentence replies.
    timeout_seconds: float | None = Field(default=None, gt=0)
    verified_on: str | None = None
    notes: str = ""


class RouteDefault(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary: str = Field(min_length=1)
    fallbacks: list[str] = Field(default_factory=list)


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    notes: str = ""
    models: list[ModelCapabilities]
    default_routes: dict[ModelRole, RouteDefault]


# What each role needs from a model. Checked at startup for every configured
# route and again by the router before each call.
ROLE_REQUIREMENTS: dict[ModelRole, dict[str, object]] = {
    "planner": {
        "modality": "generation",
        "supports_structured_output": True,
        "supports_tools": True,
    },
    "extractor": {"modality": "generation", "supports_structured_output": True},
    "drafter": {"modality": "generation"},
    "evaluator": {"modality": "generation", "supports_structured_output": True},
    "embedding": {"modality": "embedding"},
    "reranker": {"modality": "rerank"},
}


class CapabilityRegistry:
    """Lookup of ``ModelCapabilities`` by model id plus the catalog's default routes."""

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog
        self._by_model = {entry.model: entry for entry in catalog.models}
        if len(self._by_model) != len(catalog.models):
            raise ConfigurationError(
                "Model catalog lists a model more than once",
                problems=["MODEL_CAPABILITIES_FILE: duplicate model ids"],
            )

    @classmethod
    def load(cls, path: Path | None = None) -> CapabilityRegistry:
        """Load the default catalog or ``path``; every problem becomes a ``ConfigurationError``."""
        catalog_path = path or DEFAULT_CATALOG_PATH
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog = ModelCatalog.model_validate(raw)
        except OSError as exc:
            raise ConfigurationError(
                f"Model catalog is unreadable: {catalog_path.name}",
                problems=[f"MODEL_CAPABILITIES_FILE: {exc.strerror or 'unreadable'}"],
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise ConfigurationError(
                f"Model catalog is invalid: {catalog_path.name}",
                problems=[f"MODEL_CAPABILITIES_FILE: {exc}"],
            ) from exc
        missing = [role for role in MODEL_ROLES if role not in catalog.default_routes]
        if missing:
            raise ConfigurationError(
                "Model catalog is missing default routes",
                problems=[f"MODEL_CAPABILITIES_FILE: no default route for {r}" for r in missing],
            )
        return cls(catalog)

    @property
    def version(self) -> str:
        return self._catalog.version

    @property
    def default_routes(self) -> dict[ModelRole, RouteDefault]:
        return dict(self._catalog.default_routes)

    def models(self) -> list[ModelCapabilities]:
        return list(self._catalog.models)

    def get(self, model: str) -> ModelCapabilities | None:
        return self._by_model.get(model)

    def satisfies(self, model: str, role: ModelRole) -> list[str]:
        """Return the reasons ``model`` cannot serve ``role`` (empty when it can)."""
        capabilities = self.get(model)
        if capabilities is None:
            return [f"model {model!r} is not in the model catalog"]
        problems: list[str] = []
        for attribute, expected in ROLE_REQUIREMENTS[role].items():
            actual = getattr(capabilities, attribute)
            if actual != expected:
                problems.append(
                    f"model {model!r} cannot serve role {role!r}: "
                    f"{attribute} is {actual!r}, role needs {expected!r}"
                )
        return problems


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "ROLE_REQUIREMENTS",
    "CapabilityRegistry",
    "ModelCapabilities",
    "ModelCatalog",
    "ProviderName",
    "RouteDefault",
]
