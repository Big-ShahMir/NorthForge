"""Typed application settings loaded from environment variables.

All secrets are ``SecretStr`` so they never appear in logs or error messages.
``load_settings`` converts Pydantic validation failures into a readable
``ConfigurationError`` that names each offending variable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from northforge.core.errors import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

AppEnv = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_env: AppEnv = "development"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Durable dependencies (required)
    database_url: str = Field(description="PostgreSQL DSN")
    redis_url: str = Field(description="Redis DSN")

    # Worker liveness and readiness probes
    worker_health_check_interval_seconds: int = Field(default=10, ge=1, le=300)
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Authentication
    auth_mode: Literal["clerk", "dev"] = "clerk"
    clerk_secret_key: SecretStr | None = None
    clerk_jwks_url: str | None = None
    clerk_issuer: str | None = None
    clerk_authorized_parties: list[str] = Field(default_factory=list)

    # Database
    database_pool_size: int = 5
    database_echo: bool = False

    # Model provider (NVIDIA) - required from Phase 4 onward
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model_planner: str | None = None
    nvidia_model_extraction: str | None = None
    nvidia_model_drafter: str | None = None
    nvidia_model_evaluator: str | None = None

    # Object storage (S3-compatible) - required from Phase 3 onward
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def _validate_auth_mode(self) -> Self:
        if self.auth_mode == "clerk" and (self.clerk_jwks_url is None or self.clerk_issuer is None):
            raise ValueError("AUTH_MODE: clerk mode requires CLERK_JWKS_URL and CLERK_ISSUER")
        if self.auth_mode == "dev" and self.app_env == "production":
            raise ValueError("AUTH_MODE: dev mode is not allowed when APP_ENV=production")
        return self


def _format_problems(exc: ValidationError) -> list[str]:
    problems: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        problems.append(f"{location.upper()}: {message}" if location else message)
    return problems


def load_settings(*, env_file: Path | str | None = DEFAULT_ENV_FILE) -> Settings:
    """Load settings, raising ``ConfigurationError`` with every problem listed.

    ``env_file=None`` disables .env loading (used by tests).
    """
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = _format_problems(exc)
        raise ConfigurationError(
            f"Invalid configuration ({len(problems)} problem(s)): " + "; ".join(problems),
            problems=problems,
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
