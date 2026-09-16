"""Tool-layer error types.

Every failure raised anywhere in the tool pipeline is a ``ToolError``: it
carries the stable ``code``/``status_code`` pair from ``AppError``, plus a
``failure_category`` drawn from the evaluation failure taxonomy
(``docs/EVALUATION_SPEC.md``) and a ``retryable`` flag, so the runtime can
classify and retry a failure without ever inspecting exception text.
Classification happens once, in ``northforge.tools.invoke``, never inside a
tool implementation (implementations only ever raise ``ToolExecutionError``
for a provider-side failure; blocking, argument, and output errors are
raised solely by the invoker).
"""

from __future__ import annotations

from typing import Literal

from northforge.core.errors import AppError

FailureCategory = Literal[
    "wrong_tool",
    "malformed_tool_arguments",
    "provider_error",
    "timeout",
]


class ToolError(AppError):
    """Base class for tool-layer failures."""

    failure_category: FailureCategory
    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class ToolBlockedError(ToolError):
    """Raised when a tool is unregistered or absent from the caller's ``allowed_tools``."""

    code = "TOOL_BLOCKED"
    status_code = 403
    failure_category: FailureCategory = "wrong_tool"
    retryable = False


class ToolArgumentError(ToolError):
    """Raised when raw arguments fail validation against the tool's input model."""

    code = "MALFORMED_TOOL_ARGUMENTS"
    status_code = 422
    failure_category: FailureCategory = "malformed_tool_arguments"
    retryable = False


class ToolExecutionError(ToolError):
    """Raised by a tool implementation, or by the invoker on timeout.

    ``retryable`` must be supplied explicitly by the caller: a timeout is
    always retryable; a provider failure declares its own retryability.
    """

    code = "TOOL_FAILED"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        failure_category: Literal["provider_error", "timeout"] = "provider_error",
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.failure_category: FailureCategory = failure_category


class ToolOutputError(ToolError):
    """Raised when a tool's return value fails validation against its output model."""

    code = "TOOL_OUTPUT_INVALID"
    status_code = 502
    failure_category: FailureCategory = "provider_error"
    retryable = False
