"""Logging setup + a middleware that logs one line per request with a request id."""
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"))
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our own request line


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Adds X-Request-ID and logs: method path -> status in ms (user). Never logs bodies or tokens."""

    async def dispatch(self, request: Request, call_next):
        request_id = (request.headers.get("X-Request-ID") or uuid.uuid4().hex)[:64]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            user = getattr(request.state, "username", "-")
            logging.getLogger("app.request").info(
                "%s %s -> %s in %.1f ms (user=%s)", request.method, request.url.path, status_code, elapsed_ms, user
            )
            request_id_var.reset(token)
