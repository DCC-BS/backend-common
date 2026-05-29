"""Postprocessing utilities for agent outputs."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


class PostprocessingContext(BaseModel):
    index: int
    is_partial: bool


def trim_text(text: Any, context: PostprocessingContext) -> Any:
    """Remove blank lines from start of text."""
    if not isinstance(text, str):
        raise TypeError("Input must be a string")

    if context.is_partial and context.index != 0:
        return text

    return text.lstrip()


def replace_eszett(obj: Any, _: PostprocessingContext) -> Any:
    """Recursively replace ß with ss in all string fields."""
    if isinstance(obj, str):
        return obj.replace("ß", "ss")
    elif isinstance(obj, BaseModel):
        updates = {f: replace_eszett(getattr(obj, f), _) for f in type(obj).model_fields}
        return obj.model_copy(update=updates)
    elif isinstance(obj, Mapping):
        return {k: replace_eszett(v, _) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_eszett(item, _) for item in obj]
    return obj
