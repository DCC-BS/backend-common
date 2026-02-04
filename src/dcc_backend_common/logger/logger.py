import logging
import os
import time
import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

import structlog
import structlog.processors
from structlog.processors import CallsiteParameter
from structlog.stdlib import BoundLogger
from structlog.types import EventDict, Processor

from dcc_backend_common.config import get_env_or_throw

from .focused_traceback import FocusedTracebackFormatter


class DevTracebackStyle(StrEnum):
    """Traceback styles available in development mode."""

    FOCUSED = "focused"  # Rich traceback + focused locals for user code only
    RICH = "rich"  # Default Rich traceback with full locals for all frames


class _StructlogPassthroughFormatter(logging.Formatter):
    """
    A formatter that passes through pre-formatted structlog output.
    Does NOT add exception info since structlog handles that.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Return just the message - structlog has already formatted everything
        return record.getMessage()

    def formatException(self, ei: Any) -> str:
        # Don't format exceptions - structlog's exception_formatter handles this
        return ""


# Standard library logging setup
def setup_stdlib_logging() -> None:
    """Configure standard library logging to work with structlog."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    level = getattr(logging, log_level, logging.INFO)

    # Create a handler for console output with our passthrough formatter
    handler = logging.StreamHandler()
    handler.setFormatter(_StructlogPassthroughFormatter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Disable propagation for libraries that are too verbose
    for logger_name in ["uvicorn.access"]:
        lib_logger = logging.getLogger(logger_name)
        lib_logger.propagate = False


def add_request_id(logger: BoundLogger, method_name: str, event_dict: EventDict) -> Mapping[str, Any]:
    """
    Add a request ID to the log context if it doesn't exist.

    Args:
        logger: The logger instance
        method_name: The name of the logging method
        event_dict: The event dictionary

    Returns:
        The updated event dictionary
    """
    if "request_id" not in event_dict:
        event_dict["request_id"] = str(uuid.uuid4())
    return event_dict


def add_timestamp(logger: BoundLogger, method_name: str, event_dict: EventDict) -> Mapping[str, Any]:
    """
    Add an ISO-8601 timestamp to the log entry.

    Args:
        logger: The logger instance
        method_name: The name of the logging method
        event_dict: The event dictionary

    Returns:
        The updated event dictionary
    """
    event_dict["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return event_dict


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


def init_logger() -> None:
    """
    Initialize the logger configuration based on environment.

    Environment variables:
    - IS_PROD: "true" for production (JSON output), "false" for development
    - LOG_LEVEL: Logging level (default: "INFO")
    - DEV_TRACEBACK_STYLE: Traceback style in dev mode
        - "focused" (default): Rich traceback + locals only for user code
        - "rich": Full Rich traceback with all locals (verbose)
    - LOGGER_USER_CODE_PATHS: Comma-separated paths to consider as user code
    """
    # Set up standard library logging first
    setup_stdlib_logging()

    # Define processors list for structlog
    processors: list[Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        add_timestamp,
        add_request_id,
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            ]
        ),
        structlog.processors.UnicodeDecoder(),
    ]

    # Use different renderers for development vs production
    if get_env_or_throw("IS_PROD").lower() == "true":
        # JSON renderer for production to be fluentbit compatible
        processors.append(structlog.processors.JSONRenderer())
    else:
        # For development, use configurable console renderer
        processors.append(_get_dev_console_renderer())

    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


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
