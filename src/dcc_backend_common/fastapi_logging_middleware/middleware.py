import time
import uuid
from collections.abc import Awaitable, Callable

import structlog.contextvars
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dcc_backend_common.logger import get_logger

logger = get_logger("request")

# Health probes are polled every few seconds by Kubernetes; their failures are
# already logged (deduplicated) by the health probe router itself.
EXCLUDED_PATH_PREFIXES = ("/health",)

REQUEST_ID_HEADER = "X-Request-ID"


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that binds a per-request request_id and logs request failures.

    - Binds request_id (incoming X-Request-ID header or a new UUID) into the
      structlog context, so every log line emitted while handling the request
      carries the same request_id.
    - Echoes the request_id back in the X-Request-ID response header.
    - Logs responses with status >= 400 as WARNING and unhandled exceptions
      as ERROR. Successful requests are not logged.
    - Skips /health/* endpoints entirely (the health probe router does its
      own deduplicated failure logging).
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path.startswith(EXCLUDED_PATH_PREFIXES):
            return await call_next(request)

        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method = request.method
        path = request.url.path
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(
                "request_failed",
                method=method,
                path=path,
                error=str(e),
                error_type=type(e).__name__,
                duration_s=round(time.perf_counter() - start_time, 3),
                exc_info=True,
            )
            raise

        if response.status_code >= 400:
            logger.warning(
                "request_error",
                method=method,
                path=path,
                status_code=response.status_code,
                duration_s=round(time.perf_counter() - start_time, 3),
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def add_logging_middleware(app: FastAPI) -> None:
    """Add the logging middleware to a FastAPI application."""
    app.add_middleware(LoggingMiddleware)
