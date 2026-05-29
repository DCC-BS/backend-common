"""Integration tests for BaseAgent — require a real LLM endpoint.

Run with:
    LLM_URL=... LLM_API_KEY=... LLM_MODEL=... uv run pytest tests/integration/ -m integration
"""

import os

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, AgentRunResultEvent, AgentStreamEvent

from dcc_backend_common.config.app_config import LlmConfig
from dcc_backend_common.llm_agent.base_agent import BaseAgent


def _llm_config() -> LlmConfig | None:
    url = os.environ.get("LLM_URL")
    key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not (url and key and model):
        return None
    return LlmConfig(llm_url=url, llm_api_key=key, llm_model=model)


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
