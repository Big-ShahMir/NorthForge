"""Application error types with stable codes and user-safe messages."""

from __future__ import annotations


class AppError(Exception):
    """Base error carrying a stable code, HTTP status, and a user-safe message."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class ConfigurationError(AppError):
    """Raised when required configuration is missing or invalid at startup."""

    code = "CONFIGURATION_ERROR"
    status_code = 500

    def __init__(self, message: str, *, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
