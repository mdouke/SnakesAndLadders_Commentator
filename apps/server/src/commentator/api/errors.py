"""Consistent public API errors without leaking request payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _response(
    request: Request, status_code: int, code: str, message: str,
    context: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": _request_id(request)},
        content={
            "detail": {
                "code": code,
                "message": message,
                "context": context or {},
                "requestId": _request_id(request),
            }
        },
    )


def install_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return _response(
            request, error.status_code, error.code, error.message, error.context
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "location": [str(item) for item in detail["loc"]],
                "type": str(detail["type"]),
                "message": str(detail["msg"]),
            }
            for detail in error.errors()
        ]
        return _response(
            request, 422, "request_validation_failed",
            "リクエストの入力内容が不正です", {"errors": errors},
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        if isinstance(error.detail, dict):
            code = str(error.detail.get("code", "http_error"))
            message = str(error.detail.get("message", "リクエストを処理できませんでした"))
            context: dict[str, Any] = dict(error.detail.get("context", {}))
        else:
            code = "http_error"
            message = str(error.detail)
            context = {}
        return _response(request, error.status_code, code, message, context)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        return _response(
            request, 500, "internal_server_error",
            "予期しないエラーが発生しました",
        )
