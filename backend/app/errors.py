"""Consistent JSON error responses. Internal details/stack traces are never sent to clients."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("yex.errors")


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _body(code: str, message: str, request: Request, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.code, exc.message, request, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "invalid")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body("VALIDATION_ERROR", "Request validation failed", request, {"fields": fields}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return JSONResponse(status_code=exc.status_code, content=_body(code, message, request))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error request_id=%s", getattr(request.state, "request_id", None))
        return JSONResponse(
            status_code=500, content=_body("INTERNAL_ERROR", "Internal server error", request)
        )
