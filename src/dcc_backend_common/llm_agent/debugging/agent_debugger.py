import inspect
from collections.abc import AsyncGenerator, Callable
from typing import Any

from dcc_backend_common.llm_agent.debugging.event_debugger import create_event_debugger
from dcc_backend_common.logger import get_logger

logger = get_logger(__name__)


def withDebbugger(fn: Callable[..., Any], name: str | None = None) -> Callable[..., Any]:
    name = name or "UnnamedAgent"

    if inspect.isasyncgenfunction(fn):

        async def gen_wrapper(*args: Any, **kwargs: Any) -> AsyncGenerator:
            if "event_stream_handler" not in kwargs:
                kwargs["event_stream_handler"] = create_event_debugger(name)
            async for item in fn(*args, **kwargs):
                yield item

        return gen_wrapper

    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"withDebbugger requires an async function or async generator function, got {fn!r}")

    async def coro_wrapper(*args: Any, **kwargs: Any) -> Any:
        if "event_stream_handler" not in kwargs:
            kwargs["event_stream_handler"] = create_event_debugger(name)
        return await fn(*args, **kwargs)  # type: ignore[misc]

    return coro_wrapper
