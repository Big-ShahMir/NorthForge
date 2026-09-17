"""Read-only model-routing status for operators and health/status endpoints.

``build_provider_status`` turns a live ``ModelRouter`` and the current
``Settings`` into a single Pydantic model that is safe to serialise
directly into an API response: it never carries the NVIDIA API key, only
whether one is configured, and the base URL is reduced to its hostname so
no path or embedded credential can leak.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel

from northforge.core.config import Settings
from northforge.providers.resilience import CircuitState
from northforge.providers.router import ModelRouter
from northforge.providers.types import MODEL_ROLES, ModelRole


class LastError(BaseModel):
    code: str
    category: str
    at: str


class RoleStatus(BaseModel):
    role: ModelRole
    enabled: bool
    provider: str | None
    model: str | None
    fallbacks: list[str]
    supports_structured_output: bool | None
    structured_output_mode: str | None
    supports_tools: bool | None
    context_window: int | None
    circuit_state: CircuitState | None
    last_error: LastError | None


class ProviderStatus(BaseModel):
    provider: str
    configured: bool
    base_url_host: str | None
    catalog_version: str
    cache_enabled: bool
    roles: list[RoleStatus]


def _base_url_host(base_url: str) -> str | None:
    """Hostname only: no path, query, or embedded credentials."""
    parsed = urlsplit(base_url)
    return parsed.hostname


def build_provider_status(router: ModelRouter, settings: Settings) -> ProviderStatus:
    snapshot = router.snapshot()
    breaker_states = router.breaker_states()
    last_errors = router.last_errors()
    role_entries = snapshot["roles"]

    configured = settings.model_provider == "mock" or settings.nvidia_api_key is not None

    roles: list[RoleStatus] = []
    for role in MODEL_ROLES:
        entry = role_entries[role]
        if not entry.get("enabled", True):
            roles.append(
                RoleStatus(
                    role=role,
                    enabled=False,
                    provider=None,
                    model=None,
                    fallbacks=[],
                    supports_structured_output=None,
                    structured_output_mode=None,
                    supports_tools=None,
                    context_window=None,
                    circuit_state=None,
                    last_error=None,
                )
            )
            continue
        model = entry["model"]
        error_info = last_errors.get(model)
        roles.append(
            RoleStatus(
                role=role,
                enabled=True,
                provider=entry["provider"],
                model=model,
                fallbacks=list(entry["fallbacks"]),
                supports_structured_output=entry["supports_structured_output"],
                structured_output_mode=entry["structured_output_mode"],
                supports_tools=entry["supports_tools"],
                context_window=entry["context_window"],
                # Breakers are created lazily; an untouched model is a closed circuit.
                circuit_state=breaker_states.get(model, "closed"),
                last_error=LastError(**error_info) if error_info is not None else None,
            )
        )

    return ProviderStatus(
        provider=settings.model_provider,
        configured=configured,
        base_url_host=_base_url_host(settings.nvidia_base_url),
        catalog_version=snapshot["catalog_version"],
        cache_enabled=snapshot["cache_enabled"],
        roles=roles,
    )


__all__ = ["LastError", "ProviderStatus", "RoleStatus", "build_provider_status"]
