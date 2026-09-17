"""Normalised model-provider errors.

Every failure that leaves the provider layer is one of these, so callers
(the router, the API error handler, the runner) reason about a stable
``code``, a ``category``, and a ``retryable`` flag rather than a provider's
HTTP status or SDK exception. Messages are user-safe: raw provider bodies
are logged server-side (truncated) by the adapter and never attached here.

``retryable`` means "the same request to the same model may succeed shortly"
(the retry policy acts on it). ``FALLBACK_CATEGORIES`` lists the categories
that justify trying the next model in a role's fallback list; malformed
output and rejected requests are deliberately excluded because they would
reproduce on any model.
"""

from __future__ import annotations

from typing import Literal

from northforge.core.errors import AppError

ProviderErrorCategory = Literal[
    "rate_limited",
    "unavailable",
    "timeout",
    "auth",
    "bad_request",
    "not_configured",
    "capability",
    "malformed_output",
]

FALLBACK_CATEGORIES: frozenset[str] = frozenset({"rate_limited", "unavailable", "timeout"})


class ProviderError(AppError):
    """Base class for every model-provider failure."""

    code = "PROVIDER_UNAVAILABLE"
    status_code = 503
    category: ProviderErrorCategory = "unavailable"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.request_id = request_id
        if retryable is not None:
            self.retryable = retryable

    @property
    def fallback_eligible(self) -> bool:
        return self.category in FALLBACK_CATEGORIES


class ProviderRateLimitedError(ProviderError):
    """The provider returned 429 (the hosted free tier allows 40 requests/minute per key)."""

    code = "PROVIDER_RATE_LIMITED"
    status_code = 429
    category: ProviderErrorCategory = "rate_limited"
    retryable = True

    def __init__(
        self,
        message: str = "Model provider rate limit reached; retry shortly.",
        *,
        retry_after_seconds: float | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.retry_after_seconds = retry_after_seconds


class ProviderUnavailableError(ProviderError):
    """5xx, connection failure, or an open circuit: the model cannot be reached right now."""

    code = "PROVIDER_UNAVAILABLE"
    status_code = 503
    category: ProviderErrorCategory = "unavailable"
    retryable = True


class ProviderTimeoutError(ProviderUnavailableError):
    """The request exceeded its timeout; same user-facing code as unavailable."""

    category: ProviderErrorCategory = "timeout"
    retryable = True


class ProviderCircuitOpenError(ProviderUnavailableError):
    """The model's circuit breaker is open after repeated failures; not retried in place."""

    category: ProviderErrorCategory = "unavailable"
    retryable = False


class ProviderAuthError(ProviderUnavailableError):
    """401/403 from the provider. Surfaced as unavailable so no credential detail leaks."""

    category: ProviderErrorCategory = "auth"
    retryable = False

    def __init__(
        self,
        message: str = "Model provider rejected the configured credentials; check NVIDIA_API_KEY.",
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]


class ProviderNotConfiguredError(ProviderError):
    """No credentials or provider configured for the requested role."""

    code = "PROVIDER_NOT_CONFIGURED"
    status_code = 503
    category: ProviderErrorCategory = "not_configured"
    retryable = False


class ProviderRequestError(ProviderError):
    """The provider rejected the request itself (400/404/422): unknown model, bad parameters."""

    code = "PROVIDER_REQUEST_REJECTED"
    status_code = 502
    category: ProviderErrorCategory = "bad_request"
    retryable = False


class ProviderCapabilityError(ProviderError):
    """The selected model cannot perform the requested operation (for example, no tool use)."""

    code = "PROVIDER_CAPABILITY_MISMATCH"
    status_code = 500
    category: ProviderErrorCategory = "capability"
    retryable = False


class ProviderMalformedOutputError(ProviderError):
    """The model's output could not be parsed against the requested schema after one repair.

    ``raw_text`` is kept on the exception for server-side debugging only and
    must never be copied into an API response or a trace event.
    """

    code = "PROVIDER_MALFORMED_OUTPUT"
    status_code = 502
    category: ProviderErrorCategory = "malformed_output"
    retryable = False

    def __init__(
        self,
        message: str = "Model output did not match the expected schema.",
        *,
        raw_text: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.raw_text = raw_text


__all__ = [
    "FALLBACK_CATEGORIES",
    "ProviderAuthError",
    "ProviderCapabilityError",
    "ProviderCircuitOpenError",
    "ProviderError",
    "ProviderErrorCategory",
    "ProviderMalformedOutputError",
    "ProviderNotConfiguredError",
    "ProviderRateLimitedError",
    "ProviderRequestError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
]
