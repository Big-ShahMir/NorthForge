from __future__ import annotations

import pytest

from northforge.core.config import Settings, load_settings
from northforge.core.errors import ConfigurationError
from tests.conftest import set_unit_s3_env


def test_missing_required_variables_are_all_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    set_unit_s3_env(monkeypatch)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    problems = excinfo.value.problems
    assert any(p.startswith("DATABASE_URL:") for p in problems)
    assert any(p.startswith("REDIS_URL:") for p in problems)
    assert excinfo.value.code == "CONFIGURATION_ERROR"


def test_missing_s3_variables_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    problems = excinfo.value.problems
    assert any(p.startswith("S3_ENDPOINT:") for p in problems)
    assert any(p.startswith("S3_BUCKET:") for p in problems)
    assert any(p.startswith("S3_ACCESS_KEY:") for p in problems)
    assert any(p.startswith("S3_SECRET_KEY:") for p in problems)


def test_invalid_enum_value_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("APP_ENV", "staging")
    set_unit_s3_env(monkeypatch)

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


def test_s3_secrets_are_redacted_in_repr(settings: Settings) -> None:
    assert settings.s3_secret_key.get_secret_value() not in repr(settings)
    assert settings.s3_secret_key.get_secret_value() not in settings.model_dump_json()


def test_defaults(settings: Settings) -> None:
    assert settings.app_env == "test"
    assert settings.api_port == 8000
    assert settings.is_production is False


def test_clerk_mode_without_jwks_or_issuer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("AUTH_MODE", "clerk")
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    set_unit_s3_env(monkeypatch)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert any(
        "AUTH_MODE" in p and "CLERK_JWKS_URL" in p and "CLERK_ISSUER" in p
        for p in excinfo.value.problems
    )


def test_dev_mode_in_production_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert any("AUTH_MODE" in p for p in excinfo.value.problems)


def test_dev_mode_in_development_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)

    loaded = load_settings(env_file=None)

    assert loaded.auth_mode == "dev"
    assert loaded.app_env == "development"


def test_authorized_parties_accepts_empty_csv_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)

    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "")
    assert load_settings(env_file=None).clerk_authorized_parties == []

    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "http://a.test, http://b.test")
    assert load_settings(env_file=None).clerk_authorized_parties == [
        "http://a.test",
        "http://b.test",
    ]

    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", '["http://c.test"]')
    assert load_settings(env_file=None).clerk_authorized_parties == ["http://c.test"]


def test_empty_env_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)
    monkeypatch.setenv("NVIDIA_MODEL_PLANNER", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("MODEL_CACHE_ENABLED", "")

    loaded = load_settings(env_file=None)

    assert loaded.nvidia_model_planner is None
    assert loaded.nvidia_api_key is None
    assert loaded.model_cache_enabled is None
    assert loaded.model_cache_active is True  # development default


def test_model_fallbacks_accept_csv_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("AUTH_MODE", "dev")
    set_unit_s3_env(monkeypatch)

    monkeypatch.setenv("NVIDIA_MODEL_PLANNER_FALLBACKS", "a/b, c/d")
    monkeypatch.setenv("NVIDIA_MODEL_DRAFTER_FALLBACKS", '["e/f"]')
    monkeypatch.setenv("NVIDIA_MODEL_EVALUATOR_FALLBACKS", "")
    loaded = load_settings(env_file=None)

    assert loaded.nvidia_model_planner_fallbacks == ["a/b", "c/d"]
    assert loaded.nvidia_model_drafter_fallbacks == ["e/f"]
    assert loaded.nvidia_model_evaluator_fallbacks is None  # empty means unset
    assert loaded.nvidia_model_extraction_fallbacks is None


def test_mock_provider_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "clerk")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.test/jwks")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    set_unit_s3_env(monkeypatch)

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(env_file=None)

    assert any(p.startswith("MODEL_PROVIDER:") for p in excinfo.value.problems)


def test_model_provider_defaults(settings: Settings) -> None:
    assert settings.model_provider == "nvidia"
    assert settings.embedding_provider == "nvidia"
    assert settings.reranker_provider == "nvidia"
    assert settings.nvidia_rerank_base_url.startswith("https://ai.api.nvidia.com")
    assert settings.model_max_attempts == 3
    assert settings.model_cache_active is True
