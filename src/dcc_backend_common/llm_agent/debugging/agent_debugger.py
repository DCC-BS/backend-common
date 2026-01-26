from collections.abc import Callable

from dcc_backend_common.llm_agent.debugging.event_debugger import create_event_debugger
from dcc_backend_common.logger import get_logger

logger = get_logger(__name__)


def withDebbugger[TRetrun](fn: Callable[..., TRetrun], name: str | None = None) -> Callable[..., TRetrun]:
    name = name or "UnnamedAgent"

    def wrapper(*args, **kwargs) -> TRetrun:
        return fn(*args, **kwargs, event_stream_handler=create_event_debugger(name))

    return wrapper
