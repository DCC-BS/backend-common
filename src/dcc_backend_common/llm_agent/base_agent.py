"""Base agent with comprehensive pydantic AI features."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Sequence
from typing import Any, TypedDict, cast

import httpx
from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    AgentStreamEvent,
    PartDeltaEvent,
    PartStartEvent,
    UserContent,
)
from pydantic_ai.agent import NoneType
from pydantic_ai.messages import TextPart, TextPartDelta
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.result import StreamedRunResult
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from dcc_backend_common.config.app_config import LlmConfig
from dcc_backend_common.logger import get_logger

from .postprocessing import PostprocessingContext, replace_eszett, trim_text

logger = get_logger(__name__)

type UserPrompt = str | Sequence[UserContent] | None
type Preprocessor = Callable[[Any, PostprocessingContext], Any]

# Sentinel context for non-streaming (complete) output.
_FINAL = PostprocessingContext(is_partial=False, index=0)


class BaseAgent[DepsType, OutputType](ABC):
    """Abstract base class for reusable pydantic AI agents with full feature support."""

    def __init__(
        self,
        config: LlmConfig,
        deps_type: type[DepsType] | None = None,
        output_type: type[OutputType] | None = None,
        enable_thinking: bool = False,
    ):
        self.config = config
        self._enable_thinking = enable_thinking

        self.deps_type: type[Any] = deps_type if deps_type is not None else NoneType
        self.output_type: type[Any] = output_type if output_type is not None else str

        self._model_settings: OpenAIChatModelSettings = OpenAIChatModelSettings(
            timeout=config.llm_timeout,
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
        )
        if enable_thinking:
            self._model_settings["openai_reasoning_effort"] = "medium"

        # Cache once; depends only on output_type (immutable after __init__).
        self._postprocessors: list[Preprocessor] = self._get_postprocessors()
        self._stream_postprocessors: list[Preprocessor] = self._get_stream_postprocessors()

        provider = OpenAIProvider(
            base_url=config.llm_url,
            api_key=config.llm_api_key,
            http_client=self._build_http_client(),
        )

        profile = OpenAIModelProfile(
            openai_chat_supports_multiple_system_messages=False,
            openai_supports_strict_tool_definition=False,
            supports_json_schema_output=True,
            openai_responses_requires_function_call_status_none=True,
            thinking_always_enabled=enable_thinking,
        )

        self._model = OpenAIChatModel(
            config.llm_model,
            provider=provider,
            profile=profile,
        )

        self._agent = self.create_agent(self._model)

    def _build_http_client(self) -> httpx.AsyncClient:
        """httpx client with tenacity retries for transient vLLM / network errors.

        Retries on connection/transport errors and non-2xx responses (incl. 429).
        Honors the ``Retry-After`` header on rate limits, falling back to exponential
        backoff. Retry count comes from ``config.llm_max_retries``.
        """
        transport = AsyncTenacityTransport(
            config=RetryConfig(
                retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
                wait=wait_retry_after(
                    fallback_strategy=wait_exponential(multiplier=1, max=60),
                    max_wait=300,
                ),
                # +1: N retries == N+1 total attempts.
                stop=stop_after_attempt(self.config.llm_max_retries + 1),
                reraise=True,
            ),
            validate_response=lambda r: r.raise_for_status(),
        )
        return httpx.AsyncClient(transport=transport, timeout=self.config.llm_timeout)

    def _get_postprocessors(self) -> list[Preprocessor]:
        """Override to customise the postprocessing pipeline."""
        postprocessors: list[Preprocessor] = [replace_eszett]
        if self.output_type is str:
            postprocessors.append(trim_text)
        return postprocessors

    def _get_stream_postprocessors(self) -> list[Preprocessor]:
        """Override for per-delta streaming postprocessors. Default: all except trim_text."""
        return [p for p in self._postprocessors if p is not trim_text]

    def process_prompt(self, prompt: UserPrompt, deps: DepsType | None) -> UserPrompt:
        """Override to transform the prompt before it is sent to the model."""
        return prompt

    def _extract_model_settings(self, kwargs: dict[str, Any]) -> OpenAIChatModelSettings:
        """Pop model_settings from kwargs and deep-merge with the instance-level settings."""
        user_ms = kwargs.pop("model_settings", {})
        merged: dict[str, Any] = {**self._model_settings, **user_ms}
        # Deep-merge extra_body so chat_template_kwargs (enable_thinking) is never silently dropped.
        if "extra_body" in user_ms and "extra_body" in self._model_settings:
            instance_body = cast(dict[str, Any], self._model_settings["extra_body"])
            user_body = cast(dict[str, Any], user_ms["extra_body"])
            merged["extra_body"] = {**instance_body, **user_body}
        return cast(OpenAIChatModelSettings, merged)

    def _postprocess(self, output: Any) -> Any:
        for processor in self._postprocessors:
            output = processor(output, _FINAL)
        return output

    def _log_result[TOutput](self, result: AgentRunResult[TOutput] | StreamedRunResult[DepsType, TOutput]):
        usage = result.usage

        logger.info(
            "llm_call",
            extra={
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    "tool_calls": usage.tool_calls,
                    "requests": usage.requests,
                    "details": usage.details,
                },
                "finish_reason": result.response.finish_reason,
            },
        )

    @abstractmethod
    def create_agent(self, model: Model) -> Agent[DepsType, OutputType]: ...

    async def run(self, user_prompt: UserPrompt = None, deps: DepsType | None = None, **kwargs: Any) -> OutputType:
        """Run the agent and return the postprocessed output."""
        ms = self._extract_model_settings(kwargs)
        result = await self._agent.run(  # ty: ignore[no-matching-overload]
            user_prompt=self.process_prompt(user_prompt, deps), deps=deps, model_settings=ms, **kwargs
        )
        self._log_result(result)
        return self._postprocess(result.output)

    async def run_stream_text(
        self,
        user_prompt: UserPrompt = None,
        deps: DepsType | None = None,
        delta: bool = True,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream text chunks. _stream_postprocessors (e.g. replace_eszett, but not trim_text) are applied per chunk. AgentRunResultEvent is not postprocessed here."""
        generator = self.run_stream_events(user_prompt=user_prompt, deps=deps, **kwargs)

        result_text: str = ""

        async for event in generator:
            # The first piece of a text part arrives in PartStartEvent (event.part.content);
            # subsequent pieces arrive as PartDeltaEvent. Both must be emitted.
            chunk: str | None = None
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                chunk = event.part.content
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                chunk = event.delta.content_delta

            if not chunk:
                continue

            for p in self._stream_postprocessors:
                chunk = p(chunk, _FINAL)
            if not delta:
                result_text += chunk
                yield result_text
            else:
                yield chunk

    async def stream_list[T](
        self,
        user_prompt: UserPrompt = None,
        deps: DepsType | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[T, None]:
        """Stream list items progressively. Each emission is the latest state of the last item; callers receive partial updates as each item is built, then new emissions as each subsequent item starts."""
        ms = self._extract_model_settings(kwargs)

        class Container(TypedDict):
            list: list[T]

        async with self._agent.run_stream(
            user_prompt=self.process_prompt(user_prompt, deps),
            output_type=Container,
            deps=deps,
            model_settings=ms,
            **kwargs,
        ) as result:
            async for chunk in result.stream_output():
                if chunk["list"]:
                    yield self._postprocess(chunk["list"][-1])

            self._log_result(result)

    async def run_stream_output(
        self,
        user_prompt: UserPrompt = None,
        deps: DepsType | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """Stream raw structured output chunks as they are validated."""
        ms = self._extract_model_settings(kwargs)

        async with self._agent.run_stream(
            user_prompt=self.process_prompt(user_prompt, deps), deps=deps, model_settings=ms, **kwargs
        ) as result:
            async for chunk in result.stream_output():
                yield self._postprocess(chunk)

            self._log_result(result)

    async def run_stream_events(
        self,
        user_prompt: UserPrompt = None,
        deps: DepsType | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[AgentStreamEvent | AgentRunResultEvent[OutputType]]:
        """Stream raw pydantic-ai events. No postprocessing; use run() for a postprocessed final result."""
        ms = self._extract_model_settings(kwargs)

        async with self._agent.run_stream_events(  # ty: ignore[no-matching-overload]
            user_prompt=self.process_prompt(user_prompt, deps), deps=deps, model_settings=ms, **kwargs
        ) as stream:
            async for event in stream:
                yield event
