import contextvars
import enum
import logging
import os
from enum import StrEnum

import structlog
import structlog.processors
from structlog.processors import CallsiteParameter
from structlog.stdlib import BoundLogger, ProcessorFormatter
from structlog.types import EventDict, Processor, WrappedLogger

from dcc_backend_common.config import get_env_or_throw

from .focused_traceback import FocusedTracebackFormatter

USAGE_LOGGER_NAME = "usage"
"""Logger name for usage/audit events (log_event, llm_call).

Pinned to INFO in init_logger so usage events are always emitted,
regardless of LOG_LEVEL. Filter on logger="usage" in OpenSearch.
"""

EVENT_TYPES: frozenset[str] = frozenset({
    "app_event",
    "llm_call",
    "request_finished",
    "request_failed",
    "health check failed",
    "health check recovered",
})
"""Closed vocabulary of event *types* allowed in the ``event`` field.

``event`` is a contract with OpenSearch dashboards and alerting monitors:
they term-match and terms-aggregate on ``event.keyword``, which only works if
the field stays a small closed set. Any log call whose event string is not in
this set (i.e. a human-readable message — structlog puts the message into
``event`` by design) is moved to the ``message`` field at render time in
production. Extend per app via ``init_logger(extra_event_types=...)``.
"""

# Libraries whose INFO chatter (e.g. httpx "HTTP Request: ...") pollutes prod logs.
_QUIET_LIBRARIES = ("httpx", "httpcore", "openai", "urllib3", "aiohttp")

# Loggers that uvicorn / fastapi-cli attach their own (Rich) handlers to. Their
# handlers are removed so records flow through the root handler's single pipeline.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.asgi", "fastapi_cli")


class DevTracebackStyle(StrEnum):
    """Traceback styles available in development mode."""

    FOCUSED = "focused"  # Rich traceback + focused locals for user code only
    RICH = "rich"  # Default Rich traceback with full locals for all frames


def _get_dev_traceback_style() -> DevTracebackStyle:
    """
    Get the traceback style for development mode.

    Controlled by DEV_TRACEBACK_STYLE env var:
    - "focused" (default): Rich traceback + focused locals for user code only
    - "rich": Default Rich traceback with full locals for all frames
    """
    style = os.getenv("DEV_TRACEBACK_STYLE", "focused").lower()
    try:
        return DevTracebackStyle(style)
    except ValueError:
        # Fall back to focused if invalid value
        return DevTracebackStyle.FOCUSED


def _get_dev_console_renderer() -> structlog.dev.ConsoleRenderer:
    """
    Get the appropriate console renderer for development mode.

    Returns a ConsoleRenderer configured based on DEV_TRACEBACK_STYLE:
    - FOCUSED: Uses FocusedTracebackFormatter (locals only for user code)
    - RICH: Uses default Rich traceback with full locals
    """
    style = _get_dev_traceback_style()

    if style == DevTracebackStyle.RICH:
        # Default Rich traceback with full locals for all frames
        return structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.RichTracebackFormatter(
                width=120,
                max_frames=30,
                show_locals=True,
            ),
        )
    else:
        # Focused: Rich traceback + locals only for user code
        return structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=FocusedTracebackFormatter(
                width=120,
                max_frames=30,
                locals_max_string=120,
            ),
        )


def _drop_color_message_key(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Uvicorn duplicates its message with ANSI codes under "color_message" — drop it."""
    event_dict.pop("color_message", None)
    return event_dict


def _coerce_field_values(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Normalize values before they hit OpenSearch.

    - Enums are serialized via ``.value``: ``Language.EN_US`` as a chart label
      couples dashboards to Python class names; ``EN_US`` does not.
    - Trailing whitespace is stripped from ``event``: keyword buckets keep the
      newline (e.g. uvicorn's ``"Exception in ASGI application\\n"``), so term
      filters written without it silently match nothing.
    """
    event = event_dict.get("event")
    if isinstance(event, str) and event != event.rstrip():
        event_dict["event"] = event.rstrip()
    for key, value in event_dict.items():
        if isinstance(value, enum.Enum):
            event_dict[key] = value.value
    return event_dict


def _make_split_event_message(event_types: frozenset[str]) -> Processor:
    """
    Build the processor that keeps ``event`` a closed vocabulary.

    Event strings in ``event_types`` pass through untouched; anything else is
    free text and moves to ``message``, so terms aggregations and exact-term
    monitors on ``event.keyword`` never mix types with unbounded message text.
    Applied only in the production (JSON) pipeline — the dev console renderer
    needs ``event`` in place to display the line.
    """

    def split_event_message(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        event = event_dict.get("event")
        if not isinstance(event, str) or event in event_types:
            return event_dict
        existing_message = event_dict.get("message")
        event_dict["message"] = event if existing_message is None else f"{event}: {existing_message}"
        del event_dict["event"]
        return event_dict

    return split_event_message


def _make_add_app_field(app_name: str) -> Processor:
    """Stamp every rendered line with ``app`` so the JSON is self-describing
    outside the k8s context (panels no longer need the pod label)."""

    def add_app_field(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("app", app_name)
        return event_dict

    return add_app_field


asgi_exception_logged: contextvars.ContextVar[bool] = contextvars.ContextVar("asgi_exception_logged", default=False)
"""Set by LoggingMiddleware after it logs a request_failed for the current
request. Uvicorn logs its own "Exception in ASGI application" record in the
same task context, so _DropAsgiExceptionRecords can tell whether that record
is a duplicate of an already-logged failure or the only trace of one."""


class _DropAsgiExceptionRecords(logging.Filter):
    """
    Drop uvicorn's "Exception in ASGI application" records, but only when the
    logging middleware already logged the same failure as `request_failed`
    (with request_id, path, and the traceback) — then uvicorn's line is a
    second copy of the same traceback with no request context. Without the
    middleware, the uvicorn record is the only trace of the exception and is
    kept.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.getMessage().startswith("Exception in ASGI application"):
            return True
        return not asgi_exception_logged.get()


def _configure_library_loggers(library_log_levels: dict[str, int | str] | None = None) -> None:
    """
    Tame third-party loggers so the root handler is the only output path.

    ``library_log_levels`` overrides or extends the default WARNING level for
    the libraries in _QUIET_LIBRARIES, e.g. ``{"httpx": "DEBUG"}`` to re-enable
    httpx chatter in one app without touching the library defaults.
    """
    for name in _UVICORN_LOGGERS:
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True

    logging.getLogger("uvicorn.error").addFilter(_DropAsgiExceptionRecords())

    # Access logs are dropped entirely: the logging middleware reports errors,
    # and per-request 200 lines only add noise for fluentbit/OpenSearch.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False

    levels: dict[str, int | str] = dict.fromkeys(_QUIET_LIBRARIES, logging.WARNING)
    if library_log_levels:
        levels.update(library_log_levels)
    for name, lib_level in levels.items():
        logging.getLogger(name).setLevel(lib_level)


def init_logger(
    app_name: str | None = None,
    library_log_levels: dict[str, int | str] | None = None,
    extra_event_types: set[str] | None = None,
) -> None:
    """
    Initialize the logger configuration based on environment.

    Sets up a single logging pipeline: structlog events and stdlib records
    (uvicorn, third-party libraries) are all rendered by the root handler —
    JSON lines in production, a Rich console renderer in development.

    Environment variables:
    - IS_PROD: "true" for production (JSON output), "false" for development
    - LOG_LEVEL: Logging level for application diagnostics (default: "INFO").
        Usage events (logger "usage") are always emitted at INFO and up.
    - DEV_TRACEBACK_STYLE: Traceback style in dev mode
        - "focused" (default): Rich traceback + locals only for user code
        - "rich": Full Rich traceback with all locals (verbose)
    - LOGGER_USER_CODE_PATHS: Comma-separated paths to consider as user code

    Args:
        app_name: Stamped as ``app`` on every production log line so the JSON
            is self-describing outside the k8s pod-label context.
        library_log_levels: Per-library log level overrides, merged over the
            WARNING default applied to noisy libraries (httpx, httpcore, ...).
        extra_event_types: App-specific additions to EVENT_TYPES. In production,
            any event string outside the combined set is moved to ``message``.
    """
    is_prod = get_env_or_throw("IS_PROD").lower() == "true"
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            ]
        ),
        structlog.processors.UnicodeDecoder(),
        _coerce_field_values,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Processors applied to records that did NOT originate from structlog
    # (uvicorn, aiohttp, ...), so they end up with the same shape.
    foreign_pre_chain: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        _drop_color_message_key,
        _coerce_field_values,
        timestamper,
    ]

    renderer_processors: list[Processor]
    if is_prod:
        # JSON lines for fluentbit/OpenSearch; tracebacks as a string field.
        event_types = EVENT_TYPES | (extra_event_types or set())
        renderer_processors = [
            ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            _make_split_event_message(event_types),
            structlog.processors.JSONRenderer(),
        ]
        if app_name:
            renderer_processors.insert(1, _make_add_app_field(app_name))
    else:
        renderer_processors = [
            ProcessorFormatter.remove_processors_meta,
            _get_dev_console_renderer(),
        ]

    handler = logging.StreamHandler()
    handler.setFormatter(
        ProcessorFormatter(
            processors=renderer_processors,
            foreign_pre_chain=foreign_pre_chain,
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Route Python warnings (DeprecationWarning, ...) through the pipeline
    # instead of raw stderr, so they are JSON in production too.
    logging.captureWarnings(True)

    _configure_library_loggers(library_log_levels)

    # Usage events must survive any LOG_LEVEL (level is checked on the emitting
    # logger, not on root, so this wins even when root is set to WARNING).
    logging.getLogger(USAGE_LOGGER_NAME).setLevel(logging.INFO)


def get_logger(name: str | None = None) -> BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Optional name for the logger, typically the module name

    Returns:
        A bound logger instance for structured logging
    """
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()


def get_usage_logger() -> BoundLogger:
    """
    Get the logger for usage/audit events.

    Events logged here are always emitted (INFO and up), regardless of the
    LOG_LEVEL used for application diagnostics.
    """
    return structlog.get_logger(USAGE_LOGGER_NAME)
