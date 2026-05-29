"""Unit tests for postprocessing utilities."""

import pytest
from pydantic import BaseModel, ConfigDict

from dcc_backend_common.llm_agent.postprocessing import PostprocessingContext, replace_eszett, trim_text


def ctx(index: int = 0, is_partial: bool = False) -> PostprocessingContext:
    return PostprocessingContext(index=index, is_partial=is_partial)


class TestTrimText:
    def test_strips_leading_whitespace(self):
        assert trim_text("  hello", ctx()) == "hello"

    def test_strips_leading_newlines(self):
        assert trim_text("\n\nresult", ctx()) == "result"

    def test_no_strip_on_partial_non_first(self):
        assert trim_text("  hello", ctx(index=1, is_partial=True)) == "  hello"

    def test_strips_on_first_partial_chunk(self):
        assert trim_text("  hello", ctx(index=0, is_partial=True)) == "hello"

    def test_strips_on_full_output(self):
        assert trim_text("  hello", ctx(index=0, is_partial=False)) == "hello"

    def test_raises_type_error_for_non_string(self):
        with pytest.raises(TypeError, match="must be a string"):
            trim_text(42, ctx())

    def test_empty_string(self):
        assert trim_text("", ctx()) == ""


class TestReplaceEszett:
    def test_string(self):
        assert replace_eszett("Straße", ctx()) == "Strasse"

    def test_no_eszett(self):
        assert replace_eszett("hello", ctx()) == "hello"

    def test_pydantic_model(self):
        class Address(BaseModel):
            street: str

        addr = Address(street="Hauptstraße")
        result = replace_eszett(addr, ctx())
        assert result.street == "Hauptstrasse"

    def test_pydantic_model_does_not_mutate_original(self):
        class Address(BaseModel):
            street: str

        addr = Address(street="Straße")
        result = replace_eszett(addr, ctx())
        assert result is not addr
        assert addr.street == "Straße"

    def test_frozen_pydantic_model(self):
        class Frozen(BaseModel):
            model_config = ConfigDict(frozen=True)
            name: str

        obj = Frozen(name="Straße")
        result = replace_eszett(obj, ctx())
        assert result.name == "Strasse"

    def test_nested_pydantic_model(self):
        class Inner(BaseModel):
            name: str

        class Outer(BaseModel):
            inner: Inner
            label: str

        obj = Outer(inner=Inner(name="Straße"), label="Fußball")
        result = replace_eszett(obj, ctx())
        assert result.inner.name == "Strasse"
        assert result.label == "Fussball"

    def test_dict(self):
        result = replace_eszett({"key": "Straße"}, ctx())
        assert result == {"key": "Strasse"}

    def test_list(self):
        result = replace_eszett(["Straße", "Fußball"], ctx())
        assert result == ["Strasse", "Fussball"]

    def test_passthrough_non_string(self):
        assert replace_eszett(42, ctx()) == 42
        assert replace_eszett(None, ctx()) is None
        assert replace_eszett(3.14, ctx()) == 3.14
