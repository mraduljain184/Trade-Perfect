"""One error shape for the whole API (PROJECT_CONTEXT section 8):

    {"error": {"code": "...", "message": "...", "fields": [{"field", "ruleId", "message"}]}}
"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

STATUS_TO_CODE = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
}


class AppError(Exception):
    """Raise this from services/routers; the handler below turns it into the standard shape."""

    def __init__(self, status_code: int, message: str, code: str | None = None,
                 fields: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code or STATUS_TO_CODE.get(status_code, "INTERNAL_ERROR")
        self.message = message
        self.fields = fields or []


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Not authenticated"):
        super().__init__(401, message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "You do not have permission to do this"):
        super().__init__(403, message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Not found"):
        super().__init__(404, message)


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__(409, message)


class BadRequestError(AppError):
    def __init__(self, message: str):
        super().__init__(400, message)


def error_body(code: str, message: str, fields: list[dict[str, Any]] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "fields": fields or []}}


def _response(status_code: int, code: str, message: str, fields=None, headers=None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message, fields), headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return _response(exc.status_code, exc.code, exc.message, exc.fields, headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = STATUS_TO_CODE.get(exc.status_code, "BAD_REQUEST" if exc.status_code < 500 else "INTERNAL_ERROR")
        return _response(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        fields = []
        for err in exc.errors():
            location = [str(part) for part in err.get("loc", []) if part not in ("body", "query", "path")]
            fields.append({"field": ".".join(location) or "body", "ruleId": None, "message": err.get("msg", "Invalid value")})
        return _response(422, "VALIDATION_ERROR", "Request validation failed", fields)

    @app.exception_handler(Exception)
    async def _unknown_error(request: Request, exc: Exception):
        # Full details go to the log only - the client never sees a stack trace.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _response(500, "INTERNAL_ERROR", "Something went wrong. Please try again.")

    try:  # The engine (ML module) may not be merged yet - the app must still start.
        from app.engine import EngineError  # type: ignore
    except Exception:  # noqa: BLE001
        return

    @app.exception_handler(EngineError)
    async def _engine_error(request: Request, exc: Exception):
        logger.warning("Engine error on %s %s: %s", request.method, request.url.path, exc)
        return _response(400, "BAD_REQUEST", str(exc))
