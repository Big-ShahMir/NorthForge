"""Response envelope ``{data, error, request_id}`` used by every API response."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[Any] | dict[str, Any] | None = None


class Envelope[T](BaseModel):
    data: T | None = None
    error: ErrorBody | None = None
    request_id: str


def request_id_of(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else "unknown"


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[Any] | dict[str, Any] | None = None,
) -> JSONResponse:
    body = Envelope[None](
        error=ErrorBody(code=code, message=message, details=details),
        request_id=request_id_of(request),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))
