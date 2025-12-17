from typing import Any

from dspy.streaming.messages import StreamResponse
from dspy.streaming.streaming_listener import StreamListener
from litellm.types.utils import ModelResponseStream


class SwissGermanStreamListener(StreamListener):
    """Stream listener that normalizes Swiss German characters in streamed chunks."""

    def __init__(
        self,
        signature_field_name: str,
        predict: Any = None,
        predict_name: str | None = None,
        allow_reuse: bool = False,
    ):
        """
        Extend StreamListener to accept DisableReasoningAdapter (a ChatAdapter subclass).
        """
        super().__init__(
            signature_field_name=signature_field_name,
            predict=predict,
            predict_name=predict_name,
            allow_reuse=allow_reuse,
        )
        self.adapter_identifiers["DisableReasoningAdapter"] = self.adapter_identifiers["ChatAdapter"]

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Replace German sharp S with its Swiss German equivalent."""
        return value.replace("ß", "ss")

    def _normalize_chunk_fields(self, chunk: ModelResponseStream) -> ModelResponseStream:
        """Normalize both content and reasoning_content fields on a chunk."""
        delta = chunk.choices[0].delta
        if hasattr(delta, "content"):
            content = getattr(delta, "content", None)
            if content is not None:
                delta.content = self._normalize_text(content)
        if hasattr(delta, "reasoning_content"):
            reasoning_content = getattr(delta, "reasoning_content", None)
            if reasoning_content is not None:
                delta.reasoning_content = self._normalize_text(reasoning_content)
        return chunk

    def receive(self, chunk: ModelResponseStream) -> StreamResponse | None:
        """
        Intercept streamed chunks to apply Swiss German normalization before yielding.

        Normalization is applied to both standard content and reasoning content so the
        parent StreamListener can continue handling buffering and chunk parsing without
        having to know about the transformation.
        """
        normalized_chunk = self._normalize_chunk_fields(chunk)
        delta = normalized_chunk.choices[0].delta

        if getattr(delta, "content", None) is None:
            reasoning_content = getattr(delta, "reasoning_content", None)
            if reasoning_content is not None:
                delta.content = reasoning_content

        parent_result = super().receive(normalized_chunk)
        if parent_result is None:
            return None

        if isinstance(parent_result.chunk, str):
            parent_result.chunk = self._normalize_text(parent_result.chunk)
        return parent_result
