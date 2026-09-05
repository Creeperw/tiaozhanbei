from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from APP.backend.contracts.common import ApiErrorResponse, FieldError


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid.uuid4().hex)


def _normalized_detail(detail: Any, status_code: int) -> tuple[str, str]:
    default_code = _STATUS_CODES.get(status_code, "request_failed")
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("state") or default_code)
        message = str(detail.get("message") or detail.get("detail") or detail.get("error") or code)
        return code, message
    if isinstance(detail, list):
        return default_code, "Request validation failed"
    message = str(detail or default_code)
    return default_code, message


def install_api_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "").strip()
        request.state.request_id = supplied[:128] if supplied else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        code, message = _normalized_detail(exc.detail, exc.status_code)
        payload = ApiErrorResponse(
            code=code,
            detail=message,
            request_id=_request_id(request),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        field_errors = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            field_errors.append(FieldError(
                field=location,
                message=str(error.get("msg") or "Invalid value"),
                error_type=str(error.get("type") or "validation_error"),
            ))
        payload = ApiErrorResponse(
            code="validation_error",
            detail="Request validation failed",
            request_id=_request_id(request),
            field_errors=field_errors,
        )
        return JSONResponse(status_code=422, content=payload.model_dump())
