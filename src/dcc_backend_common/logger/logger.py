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


def _configure_library_loggers() -> None:
    """Tame third-party loggers so the root handler is the only output path."""
    for name in _UVICORN_LOGGERS:
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True

    # Access logs are dropped entirely: the logging middleware reports errors,
    # and per-request 200 lines only add noise for fluentbit/OpenSearch.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False

    for name in _QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)


def init_logger() -> None:
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
        timestamper,
    ]

    renderer_processors: list[Processor]
    if is_prod:
        # JSON lines for fluentbit/OpenSearch; tracebacks as a string field.
        renderer_processors = [
            ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
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

    _configure_library_loggers()

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
