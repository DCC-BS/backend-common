"""Integration tests for BaseAgent — require a real LLM endpoint.

Run with:
    LLM_URL=... LLM_API_KEY=... LLM_MODEL=... uv run pytest tests/integration/ -m integration
"""

import os

import httpx
import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, AgentRunResultEvent, AgentStreamEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, ThinkingPart, ThinkingPartDelta
from tenacity import RetryError

from dcc_backend_common.config.app_config import LlmConfig
from dcc_backend_common.llm_agent.base_agent import BaseAgent

# A prompt that a reasoning model cannot answer without at least a few thinking tokens.
REASONING_PROMPT = (
    "A bat and a ball cost 1.10 together. The bat costs 1.00 more than the ball. What does the ball cost?"
)


def _llm_config() -> LlmConfig | None:
    url = os.environ.get("LLM_URL")
    key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not (url and key and model):
        return None
    return LlmConfig(
        llm_url=url,
        llm_api_key=key,
        llm_model=model,
        llm_timeout=int(os.environ.get("LLM_TIMEOUT", "120")),
        llm_max_retries=int(os.environ.get("LLM_MAX_RETRIES", "2")),
    )


requires_llm = pytest.mark.skipif(
    _llm_config() is None,
    reason="LLM_URL, LLM_API_KEY, LLM_MODEL env vars not set",
)


class SimpleAgent(BaseAgent[None, str]):
    def create_agent(self, model):
        return Agent(model=model, system_prompt="You are a helpful assistant. Be concise.")


class WordList(BaseModel):
    words: list[str]


class WordListAgent(BaseAgent[None, WordList]):
    def create_agent(self, model):
        return Agent(model=model, output_type=WordList, system_prompt="Return structured data as instructed.")


async def _run_capturing_messages(agent: BaseAgent, prompt: str):
    """Non-streaming run that keeps the AgentRunResult.

    ``BaseAgent.run`` returns only the postprocessed output, so reasoning parts are
    unreachable through it. Go through the wrapped agent with the exact model settings
    BaseAgent built, so the model/profile/thinking wiring under test is the one exercised.
    """
    model_settings = agent._extract_model_settings({})
    return await agent._agent.run(user_prompt=prompt, model_settings=model_settings)


def _thinking_parts(messages) -> list[ThinkingPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ThinkingPart)
    ]


@requires_llm
@pytest.mark.integration
async def test_basic_run_returns_nonempty_string():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    result = await agent.run("Say only the word: hello")
    assert isinstance(result, str)
    assert len(result.strip()) > 0


@requires_llm
@pytest.mark.integration
async def test_thinking_disabled_no_think_tokens():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    result = await agent.run("What is 2+2?")
    assert "<think>" not in result
    assert "</think>" not in result


@requires_llm
@pytest.mark.integration
async def test_thinking_enabled_no_think_tokens_in_output():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=True)
    result = await agent.run("What is 2+2?")
    assert isinstance(result, str)
    assert len(result.strip()) > 0
    assert "<think>" not in result
    assert "</think>" not in result


@requires_llm
@pytest.mark.integration
async def test_run_output_has_no_leading_whitespace():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    result = await agent.run("Say only the word: hello")
    assert result == result.lstrip()


@requires_llm
@pytest.mark.integration
async def test_structured_output_run():
    config = _llm_config()
    assert config is not None
    agent = WordListAgent(config, output_type=WordList, enable_thinking=False)
    result = await agent.run("Give me a list of exactly 3 colours")
    assert isinstance(result, WordList)
    assert len(result.words) == 3
    assert all(isinstance(w, str) for w in result.words)


@requires_llm
@pytest.mark.integration
async def test_stream_text_yields_strings():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    chunks = []
    async for chunk in agent.run_stream_text("Count to 3"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert all(isinstance(c, str) for c in chunks)


@requires_llm
@pytest.mark.integration
async def test_stream_text_accumulated_grows_monotonically():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    accumulated = []
    async for text in agent.run_stream_text("Count to 5", delta=False):
        accumulated.append(text)
    assert len(accumulated) > 1
    for i in range(1, len(accumulated)):
        assert accumulated[i].startswith(accumulated[i - 1])


@requires_llm
@pytest.mark.integration
async def test_stream_text_does_not_drop_first_chunk():
    """Regression: the first text piece arrives in a PartStartEvent, not a PartDeltaEvent.

    Joining the streamed deltas must reproduce the full model output, including its
    first token. A "repeat exactly" prompt keeps the output deterministic enough to
    compare against the final (raw) result of the same run.
    """
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)

    sentence = "Da etwas buggy oder geht hier nichts"
    prompt = f"Repeat exactly this sentence and nothing else: {sentence}"

    chunks: list[str] = []
    final_output = ""
    async for event in agent.run_stream_events(prompt):
        if isinstance(event, AgentRunResultEvent):
            final_output = event.result.output
        else:
            from pydantic_ai import PartDeltaEvent, PartStartEvent
            from pydantic_ai.messages import TextPart, TextPartDelta

            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                if event.part.content:
                    chunks.append(event.part.content)
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                chunks.append(event.delta.content_delta)

    # The very first text piece (carried by PartStartEvent) must be present.
    assert len(chunks) > 0
    joined = "".join(chunks)
    # No leading content lost: reconstructed stream equals the final raw output.
    assert joined.strip() == final_output.strip()
    # And the first word survived (the exact symptom of the original bug).
    assert joined.strip().split()[0] == final_output.strip().split()[0]


@requires_llm
@pytest.mark.integration
async def test_stream_text_join_matches_nonstream_run():
    """The concatenation of run_stream_text deltas reproduces the full answer.

    Directly guards against the streamed output missing its leading chunk.
    """
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)

    sentence = "Hallo Welt dies ist ein Test"
    prompt = f"Repeat exactly this sentence and nothing else: {sentence}"

    chunks = [chunk async for chunk in agent.run_stream_text(prompt)]
    assert len(chunks) > 0
    joined = "".join(chunks).strip()
    # Full sentence reproduced, including the first word.
    assert joined.split()[0] == sentence.split()[0]
    assert sentence.split()[0] in joined


@requires_llm
@pytest.mark.integration
async def test_stream_list_yields_progressive_items():
    config = _llm_config()
    assert config is not None
    agent = WordListAgent(config, output_type=WordList, enable_thinking=False)
    emissions: list = []
    async for item in agent.stream_list("List 5 animals"):
        emissions.append(item)
    assert len(emissions) > 0
    assert all(isinstance(e, str) for e in emissions)
    # Last emission is the final complete item
    assert len(emissions[-1]) > 0


@requires_llm
@pytest.mark.integration
async def test_run_stream_output_yields_valid_chunks():
    config = _llm_config()
    assert config is not None
    agent = WordListAgent(config, output_type=WordList, enable_thinking=False)
    chunks: list[WordList] = []
    async for chunk in agent.run_stream_output("List 3 colours"):
        assert isinstance(chunk, WordList)
        chunks.append(chunk)
    assert len(chunks) > 0
    assert len(chunks[-1].words) == 3


@requires_llm
@pytest.mark.integration
async def test_run_stream_events_contains_stream_and_result_events():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    stream_events: list[AgentStreamEvent] = []
    result_events: list[AgentRunResultEvent] = []
    async for event in agent.run_stream_events("Count from 1 to 5"):
        if isinstance(event, AgentRunResultEvent):
            result_events.append(event)
        else:
            stream_events.append(event)
    assert len(stream_events) > 0
    assert len(result_events) == 1
    assert isinstance(result_events[0].result.output, str)
    assert len(result_events[0].result.output) > 0


# --- Reasoning / thinking ----------------------------------------------------


@requires_llm
@pytest.mark.integration
async def test_thinking_enabled_produces_thinking_parts_non_streaming():
    """enable_thinking=True must surface reasoning as ThinkingPart in the response."""
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=True)

    result = await _run_capturing_messages(agent, REASONING_PROMPT)

    thinking = _thinking_parts(result.all_messages())
    assert len(thinking) > 0, "no ThinkingPart returned — reasoning_content was not parsed"
    assert any(part.content.strip() for part in thinking), "ThinkingPart present but empty"


@requires_llm
@pytest.mark.integration
async def test_thinking_disabled_produces_no_thinking_parts_non_streaming():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)

    result = await _run_capturing_messages(agent, REASONING_PROMPT)

    assert _thinking_parts(result.all_messages()) == []


@requires_llm
@pytest.mark.integration
async def test_thinking_enabled_produces_thinking_events_streaming():
    """Reasoning must arrive as ThinkingPart (PartStartEvent) and ThinkingPartDelta."""
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=True)

    thinking_chunks: list[str] = []
    result_event: AgentRunResultEvent | None = None

    async for event in agent.run_stream_events(REASONING_PROMPT):
        if isinstance(event, AgentRunResultEvent):
            result_event = event
        elif isinstance(event, PartStartEvent) and isinstance(event.part, ThinkingPart):
            thinking_chunks.append(event.part.content)
        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, ThinkingPartDelta):
            thinking_chunks.append(event.delta.content_delta or "")

    assert "".join(thinking_chunks).strip(), "no reasoning content streamed"
    assert result_event is not None
    # The final result also carries the accumulated ThinkingPart.
    assert _thinking_parts(result_event.result.all_messages())


@requires_llm
@pytest.mark.integration
async def test_thinking_disabled_produces_no_thinking_events_streaming():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)

    async for event in agent.run_stream_events(REASONING_PROMPT):
        if isinstance(event, PartStartEvent):
            assert not isinstance(event.part, ThinkingPart)
        elif isinstance(event, PartDeltaEvent):
            assert not isinstance(event.delta, ThinkingPartDelta)


@requires_llm
@pytest.mark.integration
async def test_run_stream_text_excludes_reasoning_when_thinking_enabled():
    """run_stream_text yields answer text only — reasoning must not leak into the stream."""
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=True)

    chunks = [chunk async for chunk in agent.run_stream_text(REASONING_PROMPT)]
    joined = "".join(chunks)

    assert joined.strip(), "thinking enabled swallowed the answer text"
    assert "<think>" not in joined
    assert "</think>" not in joined


@requires_llm
@pytest.mark.integration
async def test_run_with_thinking_returns_answer_not_reasoning():
    """BaseAgent.run() returns the postprocessed answer, never the reasoning trace."""
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=True)

    output = await agent.run(REASONING_PROMPT)

    assert isinstance(output, str)
    assert output.strip()
    assert "<think>" not in output
    assert output == output.lstrip()


@requires_llm
@pytest.mark.integration
async def test_structured_output_with_thinking_enabled():
    """Reasoning tokens must not corrupt JSON schema output validation."""
    config = _llm_config()
    assert config is not None
    agent = WordListAgent(config, output_type=WordList, enable_thinking=True)

    result = await agent.run("Give me a list of exactly 3 colours")

    assert isinstance(result, WordList)
    assert len(result.words) == 3


@requires_llm
@pytest.mark.integration
async def test_reasoning_effort_set_only_when_thinking_enabled():
    config = _llm_config()
    assert config is not None

    assert SimpleAgent(config, enable_thinking=True)._model_settings["openai_reasoning_effort"] == "medium"
    # A literal None would be serialised as `reasoning_effort: null` and rejected by vLLM.
    assert "openai_reasoning_effort" not in SimpleAgent(config, enable_thinking=False)._model_settings


# --- Timeout / retry transport ----------------------------------------------


@requires_llm
@pytest.mark.integration
async def test_timeout_from_config_reaches_model_settings():
    config = _llm_config()
    assert config is not None
    agent = SimpleAgent(config, enable_thinking=False)
    assert agent._model_settings["timeout"] == config.llm_timeout


@pytest.mark.integration
async def test_unreachable_endpoint_reraises_underlying_transport_error():
    """AsyncTenacityTransport uses reraise=True: the httpx error surfaces, not a tenacity RetryError.

    pydantic-ai maps it to ModelAPIError, so assert on the exception chain instead.
    """
    config = LlmConfig(
        llm_url="http://127.0.0.1:1/v1",  # nothing listens on port 1
        llm_api_key="key",
        llm_model="does-not-matter",
        llm_timeout=2,
        llm_max_retries=0,  # single attempt: keeps the test fast
    )
    agent = SimpleAgent(config, enable_thinking=False)

    with pytest.raises(ModelAPIError) as exc_info:
        await agent.run("hello")

    causes = []
    err: BaseException | None = exc_info.value
    while err is not None:
        causes.append(err)
        err = err.__cause__

    assert any(isinstance(e, httpx.TransportError) for e in causes), f"transport error swallowed: {causes}"
    assert not any(isinstance(e, RetryError) for e in causes), "tenacity RetryError leaked; reraise=True is not set"
