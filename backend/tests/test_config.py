from __future__ import annotations

import pytest

from northforge.core.config import Settings, load_settings
from northforge.core.errors import ConfigurationError


def test_missing_required_variables_are_all_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    problems = excinfo.value.problems
    assert any(p.startswith("DATABASE_URL:") for p in problems)
    assert any(p.startswith("REDIS_URL:") for p in problems)
    assert excinfo.value.code == "CONFIGURATION_ERROR"


def test_invalid_enum_value_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert any(p.startswith("APP_ENV:") for p in excinfo.value.problems)


def test_secrets_are_redacted_in_repr(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-super-secret")
    loaded = load_settings(env_file=None)

    assert loaded.nvidia_api_key is not None
    assert loaded.nvidia_api_key.get_secret_value() == "nvapi-super-secret"
    assert "nvapi-super-secret" not in repr(loaded)
    assert "nvapi-super-secret" not in loaded.model_dump_json()


def test_defaults(settings: Settings) -> None:
    assert settings.app_env == "test"
    assert settings.api_port == 8000
    assert settings.is_production is False
