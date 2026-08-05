import time
import uuid
from collections.abc import Awaitable, Callable

import structlog.contextvars
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from dcc_backend_common.logger import get_logger
from dcc_backend_common.logger.logger import asgi_exception_logged

logger = get_logger("request")

# Health probes are polled every few seconds by Kubernetes; logging every probe
# would dominate the index and skew latency percentiles. Probe *failures* are
# still logged (see dispatch), and the health probe router does its own
# deduplicated dependency-failure logging.
DEFAULT_EXCLUDED_PATHS: frozenset[str] = frozenset({"/health"})

REQUEST_ID_HEADER = "X-Request-ID"

# Emitted as `path` when no route matched (404); see _route_template.
UNMATCHED_PATH = "<unmatched>"


def _route_template(request: Request) -> str:
    """
    The matched route's path template (e.g. "/items/{item_id}").

    Never the concrete URL: `path` is used as a terms-aggregation bucket in
    OpenSearch, and concrete paths would produce unbounded cardinality and can
    carry user data. Returns UNMATCHED_PATH when routing did not match (404).
    Only reliable after routing ran, i.e. after `call_next` returned or raised
    from within the endpoint.
    """
    route = request.scope.get("route")
    if isinstance(route, Route) or hasattr(route, "path_format"):
        return route.path_format  # type: ignore[union-attr]
    if hasattr(route, "path"):
        return route.path  # type: ignore[union-attr]
    return UNMATCHED_PATH


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that binds a per-request request_id and emits per-request events.

    - Binds request_id (incoming X-Request-ID header or a new UUID) into the
      structlog context, so every log line emitted while handling the request
      carries the same request_id.
    - Echoes the request_id back in the X-Request-ID response header.
    - Emits exactly one completion event per request:
        * `request_finished` (INFO) with method, path, status_code, duration_s
        * `request_failed` (ERROR) with the same fields plus the traceback when
          an unhandled exception escapes; the exception is re-raised unchanged.
    - `path` is always the route template ("/items/{item_id}"), never the
      concrete URL, and "<unmatched>" for 404s — the field is a terms bucket in
      OpenSearch and must stay low-cardinality and free of user data.
    - Excluded paths produce no `request_finished`, but still produce
      `request_failed` when they raise — probe failures are exactly what
      alerting needs to see. Each entry in `excluded_paths` is matched as a
      prefix against both the raw URL path and the matched route template, so
      high-frequency polling endpoints can be excluded by template
      (e.g. "/task/{task_id}/status") without excluding sibling routes.
    - OPTIONS requests (CORS preflights) are never logged as
      `request_finished`: they short-circuit in the CORS middleware before
      routing and would pile up as "<unmatched>" noise.

    Note on streaming responses: `call_next` returns when response *headers*
    are ready, not when the body finished streaming. For streaming endpoints
    `duration_s` is therefore time-to-first-byte, not total transfer time —
    keep that in mind when reading latency percentiles.
    """

    def __init__(self, app, *, excluded_paths: set[str] | frozenset[str] | None = None) -> None:
        super().__init__(app)
        self._excluded_paths = tuple(excluded_paths if excluded_paths is not None else DEFAULT_EXCLUDED_PATHS)

    def _is_excluded(self, request: Request, path_template: str) -> bool:
        return request.url.path.startswith(self._excluded_paths) or path_template.startswith(self._excluded_paths)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method = request.method
        start_time = time.perf_counter()
        asgi_exception_logged.set(False)

        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request_failed",
                method=method,
                path=_route_template(request),
                status_code=500,
                duration_s=round(time.perf_counter() - start_time, 4),
                exc_info=True,
            )
            # Tells the uvicorn.error filter that this failure is recorded, so
            # uvicorn's duplicate "Exception in ASGI application" line is dropped.
            asgi_exception_logged.set(True)
            raise

        path_template = _route_template(request)
        if method != "OPTIONS" and not self._is_excluded(request, path_template):
            logger.info(
                "request_finished",
                method=method,
                path=path_template,
                status_code=response.status_code,
                duration_s=round(time.perf_counter() - start_time, 4),
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def add_logging_middleware(app: FastAPI, *, excluded_paths: set[str] | None = None) -> None:
    """
    Add the logging middleware to a FastAPI application.

    Args:
        excluded_paths: Prefixes matched against the raw URL path and the route
            template; matching requests emit no `request_finished` (they still
            emit `request_failed` on exceptions). Defaults to {"/health"}.
            Include the default when overriding, e.g.
            {"/health", "/task/{task_id}/status"}.
    """
    app.add_middleware(LoggingMiddleware, excluded_paths=excluded_paths)
