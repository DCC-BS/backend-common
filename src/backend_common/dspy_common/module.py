import logging
from collections.abc import AsyncGenerator, AsyncIterator

import dspy
from dspy.streaming.messages import StreamResponse

from .adapters import DisableReasoningAdapter

Chunk = StreamResponse | str
logger = logging.getLogger(__name__)


class AbstractDspyModule(dspy.Module):
    """
    Base module that handles adapter selection and
    streaming boilerplate so subclasses only implement DSPy logic.
    """

    def _adapter_for(self, reasoning: bool) -> dspy.ChatAdapter:
        """Choose the correct adapter depending on whether reasoning is needed."""
        return dspy.ChatAdapter() if reasoning else DisableReasoningAdapter()

    def predict_with_context(self, **kwargs: object) -> dspy.Prediction:
        """Implement DSPy prediction logic inside the prepared context."""
        raise NotImplementedError("Override predict_with_context in subclasses.")

    def stream_with_context(self, **kwargs: object) -> AsyncIterator[Chunk]:
        """Implement DSPy streaming logic inside the prepared context."""
        raise NotImplementedError("Override stream_with_context in subclasses.")

    def forward(self, reasoning: bool = False, **kwargs: object) -> dspy.Prediction:
        """
        Execute a single prediction with automatic adapter selection.

        Subclasses only implement predict_with_context, which runs inside the
        dspy.context created here.
        """
        adapter = self._adapter_for(reasoning)
        with dspy.context(adapter=adapter):
            return self.predict_with_context(**kwargs)

    def stream(self, reasoning: bool = False, **kwargs: object) -> AsyncGenerator[str]:
        """
        Stream text chunks from the DSPy model with automatic adapter selection.

        Subclasses implement stream_with_context and yield StreamResponse or
        raw strings. This wrapper normalizes them to plain text for callers.
        """
        adapter = self._adapter_for(reasoning)

        async def generate_chunks():
            with dspy.context(adapter=adapter):
                async for chunk in self.stream_with_context(**kwargs):
                    text_chunk = self._chunk_text(chunk)
                    if text_chunk is None:
                        continue
                    else:
                        yield text_chunk

        return generate_chunks()

    @staticmethod
    def _chunk_text(chunk: Chunk) -> str | None:
        if isinstance(chunk, StreamResponse):
            return chunk.chunk
