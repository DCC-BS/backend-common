"""Unit tests for the log contract enforced by the production (JSON) pipeline.

All apps share one OpenSearch index, where each field's type is fixed by the
first document of the day: reserved fields must keep their type and custom
fields must stay flat scalars.
"""

import io
import json
import logging

import pytest
import structlog

from dcc_backend_common.logger import get_logger, init_logger
from dcc_backend_common.logger.logger import LEVEL_NAMES, _enforce_contract_types, _normalize_level


def enforce(event_dict: dict) -> dict:
    out = _enforce_contract_types(None, "info", event_dict)
    assert isinstance(out, dict)
    return out


class TestFlatScalars:
    def test_scalars_pass_through(self):
        out = enforce({"count": 3, "ratio": 0.5, "ok": True, "name": "x", "missing": None})
        assert out == {"count": 3, "ratio": 0.5, "ok": True, "name": "x", "missing": None}

    def test_containers_moved_to_json_suffixed_key(self):
        # A new string field must never share a name with the object other
        # apps (or older library versions) still send: OpenSearch rejects the
        # whole document on such a type clash.
        out = enforce({"options": {"a": 1, "b": [1, 2]}, "ids": [1, 2], "pair": ("a", 1)})
        assert out == {
            "options_json": '{"a": 1, "b": [1, 2]}',
            "ids_json": "[1, 2]",
            "pair_json": '["a", 1]',
        }

    def test_set_serialized_as_sorted_json_list(self):
        assert enforce({"tags": {"b", "a"}}) == {"tags_json": '["a", "b"]'}

    def test_reserved_field_container_keeps_its_name(self):
        assert enforce({"path": ["a"]}) == {"path": '["a"]'}

    @pytest.mark.parametrize(
        ("value", "expected"), [(float("nan"), "nan"), (float("inf"), "inf"), (-float("inf"), "-inf")]
    )
    def test_non_finite_custom_float_stringified(self, value, expected):
        assert enforce({"score": value})["score"] == expected

    def test_non_finite_float_inside_container_stays_valid_json(self):
        out = enforce({"scores": [float("nan")]})
        assert out["scores_json"] == "[nan]"

    def test_object_with_broken_str_falls_back_to_repr(self):
        class Broken:
            def __str__(self) -> str:
                raise RuntimeError("no str")

            def __repr__(self) -> str:
                return "<Broken>"

        assert enforce({"obj": Broken()})["obj"] == "<Broken>"
        assert enforce({"items": [Broken()]})["items_json"] == '["<Broken>"]'

    def test_object_with_broken_str_and_repr_is_unprintable(self):
        class Hopeless:
            def __str__(self) -> str:
                raise RuntimeError("no str")

            def __repr__(self) -> str:
                raise RuntimeError("no repr")

        assert enforce({"obj": Hopeless()})["obj"] == "<unprintable>"
        assert enforce({"error": Hopeless()})["error"] == "<unprintable>"

    def test_nested_non_json_values_fall_back_to_str(self):
        class Thing:
            def __str__(self) -> str:
                return "thing"

        assert enforce({"payload": {"t": Thing()}})["payload_json"] == '{"t": "thing"}'

    def test_arbitrary_object_uses_str(self):
        class Thing:
            def __str__(self) -> str:
                return "thing"

        assert enforce({"obj": Thing()})["obj"] == "thing"


class TestExceptionValues:
    def test_error_exception_becomes_message_and_type(self):
        out = enforce({"error": TimeoutError("took too long")})
        assert out["error"] == "took too long"
        assert out["error_type"] == "TimeoutError"

    def test_caller_error_type_is_kept(self):
        out = enforce({"error": ValueError("bad"), "error_type": "Custom"})
        assert out["error_type"] == "Custom"

    def test_exception_field_becomes_traceback_string(self):
        try:
            raise RuntimeError("kaputt")
        except RuntimeError as exc:
            out = enforce({"exception": exc})
        assert out["exception"].startswith("Traceback (most recent call last)")
        assert out["exception"].endswith("RuntimeError: kaputt")
        assert out["error_type"] == "RuntimeError"

    def test_exception_in_custom_field_uses_str(self):
        assert enforce({"cause": KeyError("k")})["cause"] == "'k'"


class TestReservedFieldTypes:
    @pytest.mark.parametrize(("value", "expected"), [("200", 200), (404.0, 404), (500, 500)])
    def test_status_code_coerced_to_int(self, value, expected):
        out = enforce({"status_code": value})
        assert out["status_code"] == expected
        assert type(out["status_code"]) is int

    @pytest.mark.parametrize("value", ["OK", 200.5, True, {"code": 200}])
    def test_unconvertible_status_code_moved_to_raw(self, value):
        out = enforce({"status_code": value})
        assert "status_code" not in out
        assert out["status_code_raw"] == str(value)

    @pytest.mark.parametrize(("value", "expected"), [("0.25", 0.25), (2, 2.0), (1.5, 1.5)])
    def test_duration_coerced_to_float(self, value, expected):
        out = enforce({"duration_s": value})
        assert out["duration_s"] == expected
        assert type(out["duration_s"]) is float

    @pytest.mark.parametrize("value", ["fast", "nan", False, float("nan"), float("inf"), float("-inf")])
    def test_unconvertible_duration_moved_to_raw(self, value):
        out = enforce({"duration_s": value})
        assert "duration_s" not in out
        assert out["duration_s_raw"] == str(value)

    def test_none_status_code_kept(self):
        assert enforce({"status_code": None})["status_code"] is None

    def test_reserved_string_fields_coerced(self):
        out = enforce({"request_id": 42, "path": ["a"], "message": {"k": "v"}, "error_type": ValueError})
        assert out["request_id"] == "42"
        assert out["path"] == '["a"]'
        assert out["message"] == '{"k": "v"}'
        assert out["error_type"] == "<class 'ValueError'>"


class TestLevelNames:
    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            ("debug", "debug"),
            ("info", "info"),
            ("warning", "warning"),
            ("warn", "warning"),
            ("exception", "error"),
            ("fatal", "critical"),
            ("CRITICAL", "critical"),
            ("trace", "debug"),
            ("notset", "debug"),
            ("level 5", "debug"),
            ("level 25", "warning"),
            ("level 45", "critical"),
            (35, "error"),
            ("something", "info"),
        ],
    )
    def test_normalized(self, level, expected):
        assert _normalize_level(level) == expected

    def test_mixed_case_custom_name_resolved(self):
        logging.addLevelName(45, "Severe")
        assert _normalize_level("severe") == "critical"

    def test_record_levelno_wins_over_name(self):
        assert _normalize_level("notice", levelno=35) == "error"
        assert _normalize_level("verbose", levelno=15) == "info"


@pytest.fixture
def prod_logger(monkeypatch):
    monkeypatch.setenv("IS_PROD", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    init_logger(app_name="test-app")
    stream = io.StringIO()
    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(stream)

    def lines() -> list[dict]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    yield lines

    structlog.reset_defaults()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    logging.captureWarnings(False)


class TestProductionPipeline:
    def test_structlog_levels_are_contract_names(self, prod_logger):
        log = get_logger("contract.structlog")
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.warn("w2")
        log.error("e")
        log.exception("x")
        log.critical("c")
        log.fatal("f")
        levels = {line["level"] for line in prod_logger()}
        assert levels <= LEVEL_NAMES
        assert levels == LEVEL_NAMES

    def test_stdlib_levels_are_contract_names(self, prod_logger):
        logging.addLevelName(5, "TRACE")
        lib = logging.getLogger("contract.stdlib")
        lib.setLevel(1)
        lib.log(5, "trace")
        lib.log(3, "unnamed")
        lib.warning("w", extra={"level": 99})
        lib.fatal("f")
        lib.log(45, "between error and critical")
        logging.addLevelName(25, "Notice")
        lib.log(25, "custom mixed-case name")
        lines = prod_logger()
        assert [line["level"] for line in lines] == ["debug", "debug", "warning", "critical", "critical", "warning"]
        assert all(line["logger"] == "contract.stdlib" for line in lines)

    def test_nested_and_reserved_fields_rendered_flat(self, prod_logger):
        get_logger("contract.fields").error(
            "Language check failed",
            error=ConnectionError("refused"),
            response={"status": 503},
            status_code="503",
            timestamp=0,
            level=10,
        )
        (line,) = prod_logger()
        assert line["message"] == "Language check failed"
        assert "event" not in line
        assert line["level"] == "error"
        assert isinstance(line["timestamp"], str)
        assert line["error"] == "refused"
        assert line["error_type"] == "ConnectionError"
        assert "response" not in line
        assert line["response_json"] == '{"status": 503}'
        assert line["status_code"] == 503
        assert line["app"] == "test-app"

    def test_non_finite_duration_renders_valid_json(self, prod_logger):
        get_logger("contract.nan").info("request_finished", duration_s=float("nan"))
        (line,) = prod_logger()
        assert "duration_s" not in line
        assert line["duration_s_raw"] == "nan"

    def test_non_string_event_moves_to_message(self, prod_logger):
        get_logger("contract.event").info({"weird": True})  # ty: ignore[invalid-argument-type]
        (line,) = prod_logger()
        assert "event" not in line
        assert line["message"] == '{"weird": true}'
