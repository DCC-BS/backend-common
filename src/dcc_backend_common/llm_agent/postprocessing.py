"""Postprocessing utilities for agent outputs."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


class PostprocessingContext(BaseModel):
    index: int
    is_parial: bool


def trim_text(text: Any, context: PostprocessingContext) -> Any:
    """Remove blank lines from start of text."""
    if not isinstance(text, str):
        raise TypeError("Input must be a string")

    if context.is_parial and context.index != 0:
        return text

    return text.lstrip()


def replace_eszett(obj: Any, _: PostprocessingContext) -> Any:
    """Recursively replace ß with ss in all string fields."""
    if isinstance(obj, str):
        return obj.replace("ß", "ss")
    elif isinstance(obj, BaseModel):
        for field_name in type(obj).model_fields:
            value = getattr(obj, field_name)
            setattr(obj, field_name, replace_eszett(value, _))
        return obj
    elif isinstance(obj, Mapping):
        return {k: replace_eszett(v, _) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_eszett(item, _) for item in obj]
    return obj
