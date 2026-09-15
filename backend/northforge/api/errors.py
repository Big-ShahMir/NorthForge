"""Exception handlers that translate failures into the response envelope."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from northforge.api.envelope import error_response
from northforge.core.errors import AppError, InvalidWorkflowError

logger = logging.getLogger(__name__)

_HTTP_STATUS_CODES = {
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    details = exc.details if isinstance(exc, InvalidWorkflowError) else None
    return error_response(
        request, status_code=exc.status_code, code=exc.code, message=exc.message, details=details
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").title()
    return error_response(request, status_code=exc.status_code, code=code, message=message)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"loc": [str(part) for part in err.get("loc", ())], "msg": err.get("msg", "")}
        for err in exc.errors()
    ]
    return error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed.",
        details=details,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error", exc_info=exc)
    return error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An internal error occurred. Quote the request id when reporting it.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
