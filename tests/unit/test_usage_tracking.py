"""Unit tests for UsageTrackingService and the llm_call helper."""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from dcc_backend_common.usage_tracking import UsageTrackingService


@pytest.fixture
def service() -> UsageTrackingService:
    with patch("dcc_backend_common.usage_tracking.usage_tracking.get_usage_logger") as mock_get:
        svc = UsageTrackingService(hmac_secret="secret")  # noqa: S106
        svc._logger = mock_get.return_value
    return svc


def event_kwargs(service: UsageTrackingService) -> dict:
    return cast(MagicMock, service._logger).info.call_args[1]


class TestLogEventAction:
    def test_new_style_action_passed_through(self, service):
        service.log_event("translation.text", "user-1", text_length=42)
        assert cast(MagicMock, service._logger).info.call_args[0][0] == "app_event"
        kwargs = event_kwargs(service)
        assert kwargs["action"] == "translation.text"
        assert kwargs["text_length"] == 42
        assert "pseudonym_id" in kwargs

    def test_deprecated_module_func_still_works_with_warning(self, service):
        with pytest.warns(DeprecationWarning):
            service.log_event(module="transcribe_route", func="transcribe", user_id="user-1")
        assert event_kwargs(service)["action"] == "transcribe_route.transcribe"

    def test_nonconforming_action_warns_but_still_emits(self, service):
        with patch("dcc_backend_common.usage_tracking.usage_tracking.logger") as mock_logger:
            service.log_event("Not.A.Valid-Action", "user-1")
        mock_logger.warning.assert_called_once()
        assert event_kwargs(service)["action"] == "Not.A.Valid-Action"

    def test_missing_action_raises(self, service):
        with pytest.raises(TypeError):
            service.log_event(user_id="user-1")

    def test_pseudonym_is_stable_and_not_raw_user_id(self, service):
        a = service.get_pseudonymized_user_id("user-1")
        b = service.get_pseudonymized_user_id("user-1")
        assert a == b
        assert a != "user-1"

    def test_pseudonym_bound_into_context(self, service):
        with patch("dcc_backend_common.usage_tracking.usage_tracking.structlog.contextvars.bind_contextvars") as bind:
            service.log_event("translation.text", "user-1")
        assert bind.call_args[1]["pseudonym_id"] == service.get_pseudonymized_user_id("user-1")


class TestLogLlmCall:
    def _result(self, finish_reason: str | None, parts: list) -> MagicMock:
        result = MagicMock()
        result.usage = MagicMock(
            input_tokens=10, output_tokens=5, total_tokens=15, tool_calls=0, requests=1, details={}
        )
        result.response = MagicMock(finish_reason=finish_reason, parts=parts)
        return result

    def test_counts_tool_call_parts_by_part_kind(self):
        from dcc_backend_common.usage_tracking import log_llm_call

        part = MagicMock(part_kind="tool-call")
        text = MagicMock(part_kind="text")
        with patch("dcc_backend_common.usage_tracking.usage_tracking.get_usage_logger") as mock_get:
            log_llm_call(self._result("tool_call", [part, text]))
        kwargs = mock_get.return_value.info.call_args[1]
        assert kwargs["tool_calls"] == 1
        assert kwargs["finish_reason"] == "tool_call"
