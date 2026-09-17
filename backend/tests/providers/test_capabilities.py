"""Tests for ``northforge.providers.capabilities.CapabilityRegistry``.

``capabilities.py`` is a contract file this task does not own or modify;
these tests exercise its public behavior (``load``, ``get``, ``satisfies``,
``default_routes``, ``version``) since the router and status modules both
depend on it working as documented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northforge.core.errors import ConfigurationError
from northforge.providers.capabilities import (
    CapabilityRegistry,
    ModelCapabilities,
    ModelCatalog,
    RouteDefault,
)
from northforge.providers.types import MODEL_ROLES


def _capabilities(**overrides: object) -> ModelCapabilities:
    base: dict[str, object] = {
        "model": "test/model",
        "provider": "mock",
        "modality": "generation",
        "supports_structured_output": True,
        "structured_output_mode": "json_schema",
        "supports_tools": True,
        "supports_reasoning_toggle": False,
        "context_window": 4096,
    }
    base.update(overrides)
    return ModelCapabilities(**base)  # type: ignore[arg-type]


def _catalog(
    models: list[ModelCapabilities], default_routes: dict[str, RouteDefault]
) -> ModelCatalog:
    return ModelCatalog(version="test-catalog", models=models, default_routes=default_routes)  # type: ignore[arg-type]


# --- load: the real, shipped catalog ---------------------------------------


def test_load_default_catalog_succeeds() -> None:
    registry = CapabilityRegistry.load()
    assert registry.version
    for role in MODEL_ROLES:
        assert role in registry.default_routes


def test_load_missing_file_raises_configuration_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(ConfigurationError) as excinfo:
        CapabilityRegistry.load(missing)
    assert any("MODEL_CAPABILITIES_FILE" in p for p in excinfo.value.problems)


def test_load_invalid_json_raises_configuration_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "catalog.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        CapabilityRegistry.load(bad_file)


def test_load_catalog_missing_default_route_raises(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    payload = {
        "version": "test",
        "models": [
            {
                "model": "m1",
                "provider": "mock",
                "modality": "generation",
                "supports_structured_output": True,
                "structured_output_mode": "json_schema",
                "supports_tools": True,
                "supports_reasoning_toggle": False,
                "context_window": 4096,
            }
        ],
        # Missing routes for every role except planner.
        "default_routes": {"planner": {"primary": "m1", "fallbacks": []}},
    }
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        CapabilityRegistry.load(catalog_path)
    problems = excinfo.value.problems
    missing_roles = {role for role in MODEL_ROLES if role != "planner"}
    assert len(problems) == len(missing_roles)


# --- constructor: duplicate model ids ---------------------------------------


def test_duplicate_model_ids_rejected() -> None:
    catalog = _catalog(
        [_capabilities(model="dup"), _capabilities(model="dup")],
        default_routes={},
    )
    with pytest.raises(ConfigurationError):
        CapabilityRegistry(catalog)


# --- get / satisfies ---------------------------------------------------------


def test_get_returns_none_for_unknown_model() -> None:
    catalog = _catalog([_capabilities(model="known")], default_routes={})
    registry = CapabilityRegistry(catalog)
    assert registry.get("known") is not None
    assert registry.get("unknown") is None


def test_satisfies_unknown_model_reports_not_in_catalog() -> None:
    catalog = _catalog([_capabilities(model="known")], default_routes={})
    registry = CapabilityRegistry(catalog)
    problems = registry.satisfies("unknown", "planner")
    assert len(problems) == 1
    assert "not in the model catalog" in problems[0]


def test_satisfies_planner_requires_structured_output_and_tools() -> None:
    catalog = _catalog(
        [_capabilities(model="weak", supports_structured_output=False, supports_tools=False)],
        default_routes={},
    )
    registry = CapabilityRegistry(catalog)
    problems = registry.satisfies("weak", "planner")
    assert len(problems) == 2


def test_satisfies_passes_for_fully_capable_model() -> None:
    catalog = _catalog([_capabilities(model="strong")], default_routes={})
    registry = CapabilityRegistry(catalog)
    assert registry.satisfies("strong", "planner") == []


def test_satisfies_embedding_role_checks_modality() -> None:
    catalog = _catalog(
        [
            _capabilities(
                model="embed-1",
                modality="embedding",
                supports_structured_output=False,
                structured_output_mode=None,
                supports_tools=False,
            )
        ],
        default_routes={},
    )
    registry = CapabilityRegistry(catalog)
    assert registry.satisfies("embed-1", "embedding") == []
    problems = registry.satisfies("embed-1", "planner")
    assert any("modality" in p for p in problems)


# --- default_routes / version -------------------------------------------------


def test_default_routes_returns_copy() -> None:
    catalog = _catalog(
        [_capabilities(model="m1")],
        default_routes={"planner": RouteDefault(primary="m1", fallbacks=[])},
    )
    registry = CapabilityRegistry(catalog)
    routes = registry.default_routes
    routes["planner"] = RouteDefault(primary="mutated", fallbacks=[])
    # Mutating the returned dict must not affect the registry's internal state.
    assert registry.default_routes["planner"].primary == "m1"


def test_version_reflects_catalog() -> None:
    catalog = _catalog([_capabilities(model="m1")], default_routes={})
    registry = CapabilityRegistry(catalog)
    assert registry.version == "test-catalog"
