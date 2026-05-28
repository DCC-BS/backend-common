"""Base agent with comprehensive pydantic AI features."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Sequence
from typing import Any, TypedDict, cast

from pydantic_ai import (
    Agent,
    AgentRunResult,
    AgentRunResultEvent,
    AgentStreamEvent,
    PartDeltaEvent,
    UserContent,
)
from pydantic_ai.agent import NoneType
from pydantic_ai.messages import TextPartDelta
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.result import StreamedRunResult

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

        self._model_settings: OpenAIChatModelSettings = {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
        }

        # Cache once; depends only on output_type (immutable after __init__).
        self._postprocessors: list[Preprocessor] = self._get_postprocessors()
        self._stream_postprocessors: list[Preprocessor] = self._get_stream_postprocessors()

        self._model = OpenAIChatModel(
            config.llm_model,
            provider=OpenAIProvider(
                base_url=config.llm_url,
                api_key=config.llm_api_key,
            ),
        )

        self._agent = self.create_agent(self._model)

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
        result = await self._agent.run(  # type: ignore
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
        """Stream raw text deltas. Postprocessing is applied to the final assembled result event."""
        generator = self.run_stream_events(user_prompt=user_prompt, deps=deps, **kwargs)

        result_text: str = ""

        async for event in generator:
            if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                chunk = event.delta.content_delta
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

        async with self._agent.run_stream_events(  # type: ignore
            user_prompt=self.process_prompt(user_prompt, deps), deps=deps, model_settings=ms, **kwargs
        ) as stream:
            async for event in stream:
                yield event
