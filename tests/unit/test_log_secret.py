import pytest

from dcc_backend_common.config import log_secret


@pytest.mark.parametrize(
    "secret,expected",
    [
        (None, "None"),
        ("", "None"),
        ("a", "a"),
        ("ab", "ab"),
        ("abc", "ab*"),
        ("token", "to***"),
        ("secret123", "se*******"),
    ],
)
def test_log_secret(secret: str | None, expected: str) -> None:
    assert log_secret(secret) == expected


class TestCoerceFieldValues:
    def test_enum_serialized_via_value_and_event_stripped(self):
        import enum

        from dcc_backend_common.logger.logger import _coerce_field_values

        class Language(enum.Enum):
            EN_US = "EN_US"

        event_dict = {
            "event": "Exception in ASGI application\n",
            "target_language": Language.EN_US,
            "domain": None,
        }
        out = _coerce_field_values(None, "info", event_dict)
        assert out["event"] == "Exception in ASGI application"
        assert out["target_language"] == "EN_US"
        assert out["domain"] is None


class TestSplitEventMessage:
    def _split(self, event_dict):
        from dcc_backend_common.logger.logger import EVENT_TYPES, _make_split_event_message

        out = _make_split_event_message(EVENT_TYPES)(None, "info", event_dict)
        assert isinstance(out, dict)
        return out

    def test_typed_events_keep_event_field(self):
        for event in ("app_event", "llm_call", "request_finished", "request_failed", "health check failed"):
            out = self._split({"event": event})
            assert out["event"] == event
            assert "message" not in out

    def test_free_text_moves_to_message(self):
        out = self._split({"event": "Client disconnected from advisor stream"})
        assert "event" not in out
        assert out["message"] == "Client disconnected from advisor stream"

    def test_existing_message_is_preserved(self):
        out = self._split({"event": "Detection timed out", "message": "after 30s"})
        assert out["message"] == "Detection timed out: after 30s"
        assert "event" not in out

    def test_extra_event_types_extend_vocabulary(self):
        from dcc_backend_common.logger.logger import EVENT_TYPES, _make_split_event_message

        split = _make_split_event_message(EVENT_TYPES | {"custom_event"})
        out = split(None, "info", {"event": "custom_event"})
        assert isinstance(out, dict)
        assert out["event"] == "custom_event"

    def test_app_field_stamped(self):
        from dcc_backend_common.logger.logger import _make_add_app_field

        out = _make_add_app_field("textmate")(None, "info", {"event": "app_event"})
        assert isinstance(out, dict)
        assert out["app"] == "textmate"


class TestDropAsgiExceptionRecords:
    def test_asgi_exception_record_dropped_other_records_pass(self):
        import logging

        from dcc_backend_common.logger.logger import _DropAsgiExceptionRecords

        f = _DropAsgiExceptionRecords()

        def record(msg: str) -> logging.LogRecord:
            return logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, msg, None, None)

        assert f.filter(record("Exception in ASGI application\n")) is False
        assert f.filter(record("Application startup complete.")) is True
