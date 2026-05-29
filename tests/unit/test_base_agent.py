"""Unit tests for BaseAgent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai import AgentRunResultEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import TextPart, TextPartDelta

from dcc_backend_common.llm_agent.base_agent import BaseAgent
from dcc_backend_common.llm_agent.postprocessing import replace_eszett, trim_text


def make_text_delta(content: str, part_index: int = 0) -> PartDeltaEvent:
    return PartDeltaEvent(index=part_index, delta=TextPartDelta(content_delta=content))


def make_text_start(content: str, part_index: int = 0) -> PartStartEvent:
    return PartStartEvent(index=part_index, part=TextPart(content=content))


async def fake_stream_events(*events: Any) -> AsyncIterator[Any]:
    for event in events:
        yield event


def make_run_result_event(output: str) -> AgentRunResultEvent:
    result = MagicMock()
    result.output = output
    result.usage = MagicMock(input_tokens=1, output_tokens=1, total_tokens=2, tool_calls=0, requests=1, details={})
    result.response = MagicMock(finish_reason="stop")
    event = MagicMock(spec=AgentRunResultEvent)
    event.result = result
    return event


def make_config(model: str = "test-model", url: str = "http://localhost", key: str = "key") -> Any:
    cfg = MagicMock()
    cfg.llm_model = model
    cfg.llm_url = url
    cfg.llm_api_key = key
    return cfg


class ConcreteAgent(BaseAgent[None, str]):
    def create_agent(self, model: Any) -> Any:
        return MagicMock()


class StructuredAgent(BaseAgent[None, dict]):  # type: ignore[type-arg]
    def create_agent(self, model: Any) -> Any:
        return MagicMock()


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def agent(config):
    with (
        patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
        patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
    ):
        return ConcreteAgent(config)


@pytest.fixture
def agent_thinking(config):
    with (
        patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
        patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
    ):
        return ConcreteAgent(config, enable_thinking=True)


class TestInit:
    def test_model_settings_thinking_disabled(self, agent):
        assert agent._model_settings == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}

    def test_model_settings_thinking_enabled(self, agent_thinking):
        assert agent_thinking._model_settings == {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}

    def test_default_output_type_is_str(self, agent):
        assert agent.output_type is str

    def test_default_deps_type_is_nonetype(self, agent, config):
        from pydantic_ai.agent import NoneType

        with (
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
        ):
            a = ConcreteAgent(config)
        assert a.deps_type is NoneType

    def test_custom_output_type(self, config):
        with (
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
        ):
            a = StructuredAgent(config, output_type=dict)
        assert a.output_type is dict

    def test_postprocessors_cached_on_init(self, agent):
        assert trim_text in agent._postprocessors
        assert replace_eszett in agent._postprocessors

    def test_postprocessors_no_trim_for_non_str(self, config):
        with (
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
        ):
            a = StructuredAgent(config, output_type=dict)
        assert replace_eszett in a._postprocessors
        assert trim_text not in a._postprocessors

    def test_get_postprocessors_override_respected(self, config):
        def sentinel(x, _):
            return x

        class CustomAgent(BaseAgent[None, str]):
            def create_agent(self, model: Any) -> Any:
                return MagicMock()

            def _get_postprocessors(self):
                return [sentinel]

        with (
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
        ):
            a = CustomAgent(config)
        assert a._postprocessors == [sentinel]

    async def test_process_prompt_hook_called(self, config):
        calls: list = []

        class HookAgent(BaseAgent[None, str]):
            def create_agent(self, model: Any) -> Any:
                return MagicMock()

            def process_prompt(self, prompt, deps):
                calls.append(prompt)
                return f"[wrapped] {prompt}"

        with (
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIChatModel"),
            patch("dcc_backend_common.llm_agent.base_agent.OpenAIProvider"),
        ):
            a = HookAgent(config)

        mock_result = MagicMock()
        mock_result.output = "ok"
        mock_result.usage = MagicMock(
            input_tokens=1, output_tokens=1, total_tokens=2, tool_calls=0, requests=1, details={}
        )
        mock_result.response = MagicMock(finish_reason="stop")
        a._agent.run = AsyncMock(return_value=mock_result)  # type: ignore

        await a.run("hello")
        assert calls == ["hello"]
        forwarded = a._agent.run.call_args[1]["user_prompt"]  # type: ignore
        assert forwarded == "[wrapped] hello"


class TestExtractModelSettings:
    def test_no_user_settings(self, agent):
        kwargs: dict = {"other": "value"}
        result = agent._extract_model_settings(kwargs)
        assert result == agent._model_settings

    def test_user_settings_merged(self, agent):
        kwargs: dict = {"model_settings": {"temperature": 0.5}}
        result = agent._extract_model_settings(kwargs)
        assert result["temperature"] == 0.5
        assert result["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_extra_body_deep_merged_user_key_preserved(self, agent_thinking):
        # User passes a vendor key; the instance's enable_thinking must not be dropped.
        kwargs: dict = {"model_settings": {"extra_body": {"top_k": 64}}}
        result = agent_thinking._extract_model_settings(kwargs)
        assert result["extra_body"]["top_k"] == 64
        assert result["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}

    def test_extra_body_user_key_wins_on_collision(self, agent_thinking):
        # User explicitly overrides enable_thinking; their value wins.
        kwargs: dict = {"model_settings": {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}}
        result = agent_thinking._extract_model_settings(kwargs)
        assert result["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False

    def test_model_settings_key_removed_from_kwargs(self, agent):
        kwargs: dict = {"model_settings": {"temperature": 0.5}, "other": "val"}
        agent._extract_model_settings(kwargs)
        assert "model_settings" not in kwargs


class TestPostprocess:
    def test_eszett_replaced(self, agent):
        assert agent._postprocess("Straße") == "Strasse"

    def test_leading_whitespace_trimmed(self, agent):
        assert agent._postprocess("  hello") == "hello"

    def test_both_applied(self, agent):
        assert agent._postprocess("  Straße") == "Strasse"


class TestRun:
    async def test_run_calls_agent_with_model_settings(self, agent):
        mock_result = MagicMock()
        mock_result.output = "result"
        mock_result.usage.return_value = MagicMock(
            input_tokens=1, output_tokens=1, total_tokens=2, tool_calls=0, requests=1, details={}
        )
        mock_result.response = MagicMock(finish_reason="stop")
        agent._agent.run = AsyncMock(return_value=mock_result)

        await agent.run("hello")

        call_kwargs = agent._agent.run.call_args[1]
        assert "model_settings" in call_kwargs
        assert call_kwargs["model_settings"]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    async def test_run_postprocesses_output(self, agent):
        mock_result = MagicMock()
        mock_result.output = "  Straße"
        mock_result.usage.return_value = MagicMock(
            input_tokens=1, output_tokens=1, total_tokens=2, tool_calls=0, requests=1, details={}
        )
        mock_result.response = MagicMock(finish_reason="stop")
        agent._agent.run = AsyncMock(return_value=mock_result)

        result = await agent.run("hello")
        assert result == "Strasse"


class TestStreaming:
    """Streaming methods pass raw deltas through unchanged; postprocessing happens only on AgentRunResultEvent."""

    def _mock_stream(self, agent, *events):
        @asynccontextmanager
        async def fake(**kw):
            yield fake_stream_events(*events)

        agent._agent.run_stream_events = fake

    async def _collect_text_deltas(self, agent, *events) -> list[str]:
        self._mock_stream(agent, *events)
        return [chunk async for chunk in agent.run_stream_text("prompt")]

    async def test_eszett_replaced_in_deltas(self, agent):
        events = [make_text_delta("Straße"), make_text_delta(" und Fußball")]
        result = await self._collect_text_deltas(agent, *events)
        assert result == ["Strasse", " und Fussball"]

    async def test_spaces_and_newlines_untouched(self, agent):
        events = [make_text_delta("\n"), make_text_delta("The"), make_text_delta(" answer")]
        result = await self._collect_text_deltas(agent, *events)
        assert result == ["\n", "The", " answer"]

    async def test_run_stream_events_yields_result_event_raw(self, agent):
        run_result_event = make_run_result_event("  Straße")
        self._mock_stream(agent, run_result_event)
        events = [e async for e in agent.run_stream_events("prompt")]
        assert len(events) == 1
        assert events[0].result.output == "  Straße"  # raw, not postprocessed

    async def test_run_stream_text_yields_raw_deltas(self, agent):
        run_result_event = make_run_result_event("Hello world")
        events = [make_text_delta("  Hello"), make_text_delta(" world"), run_result_event]
        self._mock_stream(agent, *events)
        chunks = [c async for c in agent.run_stream_text("prompt")]
        assert chunks == ["  Hello", " world"]

    async def test_part_start_content_emitted(self, agent):
        # First text piece arrives in PartStartEvent; it must not be dropped.
        events = [make_text_start("Da"), make_text_delta(" etwas"), make_text_delta(" buggy")]
        result = await self._collect_text_deltas(agent, *events)
        assert result == ["Da", " etwas", " buggy"]

    async def test_empty_part_start_skipped(self, agent):
        # An empty PartStartEvent (no initial content) must not yield an empty chunk.
        events = [make_text_start(""), make_text_delta("Hello"), make_text_delta(" world")]
        result = await self._collect_text_deltas(agent, *events)
        assert result == ["Hello", " world"]

    async def test_part_start_postprocessed(self, agent):
        events = [make_text_start("Straße"), make_text_delta(" gut")]
        result = await self._collect_text_deltas(agent, *events)
        assert result == ["Strasse", " gut"]

    async def test_part_start_accumulated_when_not_delta(self, agent):
        self._mock_stream(agent, make_text_start("Da"), make_text_delta(" etwas"))
        chunks = [c async for c in agent.run_stream_text("prompt", delta=False)]
        assert chunks == ["Da", "Da etwas"]


class TestWithDebbugger:
    async def test_wraps_coroutine(self):
        from dcc_backend_common.llm_agent.debugging.agent_debugger import withDebbugger

        calls: list = []

        async def my_run(event_stream_handler=None):
            calls.append(event_stream_handler)
            return "ok"

        wrapped = withDebbugger(my_run, name="test")
        result = await wrapped()
        assert result == "ok"
        assert calls[0] is not None

    async def test_wraps_async_generator(self):
        import inspect

        from dcc_backend_common.llm_agent.debugging.agent_debugger import withDebbugger

        async def my_gen(event_stream_handler=None):
            yield 1
            yield 2

        wrapped = withDebbugger(my_gen, name="test")
        assert inspect.isasyncgenfunction(wrapped)
        items = [x async for x in wrapped()]
        assert items == [1, 2]

    def test_raises_for_sync_function(self):
        from dcc_backend_common.llm_agent.debugging.agent_debugger import withDebbugger

        def sync_fn():
            return "result"

        with pytest.raises(TypeError, match="async"):
            withDebbugger(sync_fn)


class TestStreamList:
    """stream_list yields the latest state of the last item on every non-empty snapshot."""

    def _mock_run_stream(self, agent, snapshots: list[list]):
        class FakeResult:
            def stream_output(self):
                async def _gen():
                    for s in snapshots:
                        yield {"list": s}

                return _gen()

            @property
            def usage(self):
                return MagicMock(input_tokens=1, output_tokens=1, total_tokens=2, tool_calls=0, requests=1, details={})

            @property
            def response(self):
                return MagicMock(finish_reason="stop")

        @asynccontextmanager
        async def fake_run_stream(**kw):
            yield FakeResult()

        agent._agent.run_stream = fake_run_stream

    async def test_progressive_updates_yielded(self, agent):
        # Each non-empty snapshot yields chunk["list"][-1] — caller sees progressive build
        snapshots = [["h"], ["he"], ["hel"], ["hello"]]
        self._mock_run_stream(agent, snapshots)
        items = [x async for x in agent.stream_list("prompt")]
        assert items == ["h", "he", "hel", "hello"]

    async def test_empty_snapshots_skipped(self, agent):
        snapshots = [[], [], ["first"]]
        self._mock_run_stream(agent, snapshots)
        items = [x async for x in agent.stream_list("prompt")]
        assert items == ["first"]

    async def test_multiple_items_streamed(self, agent):
        snapshots = [["item1"], ["item1", "it"], ["item1", "item2"]]
        self._mock_run_stream(agent, snapshots)
        items = [x async for x in agent.stream_list("prompt")]
        assert items == ["item1", "it", "item2"]

    async def test_no_snapshots_yields_nothing(self, agent):
        self._mock_run_stream(agent, [])
        items = [x async for x in agent.stream_list("prompt")]
        assert items == []
