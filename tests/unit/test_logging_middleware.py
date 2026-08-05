"""Unit tests for the request-completion logging middleware.

The field names asserted here (event, method, path, status_code, duration_s)
are a contract with OpenSearch dashboard panels and alerting monitors.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dcc_backend_common.fastapi_logging_middleware import middleware as middleware_module
from dcc_backend_common.fastapi_logging_middleware.middleware import (
    REQUEST_ID_HEADER,
    UNMATCHED_PATH,
    add_logging_middleware,
)


class FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, **kwargs: object) -> None:
        self.calls.append((level, event, kwargs))

    def info(self, event: str, /, **kwargs: object) -> None:
        self._record("info", event, **kwargs)

    def error(self, event: str, /, **kwargs: object) -> None:
        self._record("error", event, **kwargs)


@pytest.fixture
def fake_logger(monkeypatch) -> FakeLogger:
    logger = FakeLogger()
    monkeypatch.setattr(middleware_module, "logger", logger)
    return logger


@pytest.fixture
def client(fake_logger) -> TestClient:
    app = FastAPI()
    add_logging_middleware(app)

    @app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict:
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("kaputt")

    @app.get("/health/liveness")
    async def liveness() -> dict:
        return {"status": "up"}

    @app.get("/health/broken-probe")
    async def broken_probe() -> dict:
        raise RuntimeError("probe kaputt")

    return TestClient(app, raise_server_exceptions=False)


def only(calls: list, event: str) -> list:
    return [c for c in calls if c[1] == event]


class TestRequestFinished:
    def test_success_emits_exactly_one_request_finished(self, client, fake_logger):
        response = client.get("/items/42")
        assert response.status_code == 200
        finished = only(fake_logger.calls, "request_finished")
        assert len(finished) == 1
        assert len(fake_logger.calls) == 1

    def test_path_is_route_template_not_concrete_url(self, client, fake_logger):
        client.get("/items/42")
        _, _, kwargs = only(fake_logger.calls, "request_finished")[0]
        assert kwargs["path"] == "/items/{item_id}"

    def test_field_types_match_contract(self, client, fake_logger):
        client.get("/items/42")
        _, _, kwargs = only(fake_logger.calls, "request_finished")[0]
        assert kwargs["method"] == "GET"
        assert isinstance(kwargs["status_code"], int)
        assert kwargs["status_code"] == 200
        assert isinstance(kwargs["duration_s"], float)

    def test_404_logs_unmatched_literal_not_raw_path(self, client, fake_logger):
        response = client.get("/no/such/route/12345?token=secret")
        assert response.status_code == 404
        _, _, kwargs = only(fake_logger.calls, "request_finished")[0]
        assert kwargs["path"] == UNMATCHED_PATH
        assert kwargs["status_code"] == 404

    def test_request_id_echoed_in_response_header(self, client):
        response = client.get("/items/1", headers={REQUEST_ID_HEADER: "abc-123"})
        assert response.headers[REQUEST_ID_HEADER] == "abc-123"


class TestRequestFailed:
    def test_unhandled_exception_emits_request_failed_only(self, client, fake_logger):
        response = client.get("/boom")
        assert response.status_code == 500
        failed = only(fake_logger.calls, "request_failed")
        assert len(failed) == 1
        assert only(fake_logger.calls, "request_finished") == []

        level, _, kwargs = failed[0]
        assert level == "error"
        assert kwargs["method"] == "GET"
        assert kwargs["path"] == "/boom"
        assert kwargs["status_code"] == 500
        assert isinstance(kwargs["duration_s"], float)
        assert kwargs["exc_info"] is True


class TestExcludedPaths:
    def test_probe_path_emits_no_request_finished(self, client, fake_logger):
        response = client.get("/health/liveness")
        assert response.status_code == 200
        assert fake_logger.calls == []

    def test_probe_path_that_raises_still_emits_request_failed(self, client, fake_logger):
        response = client.get("/health/broken-probe")
        assert response.status_code == 500
        failed = only(fake_logger.calls, "request_failed")
        assert len(failed) == 1
        assert failed[0][2]["path"] == "/health/broken-probe"

    def test_custom_excluded_paths(self, fake_logger):
        app = FastAPI()
        add_logging_middleware(app, excluded_paths={"/metrics"})

        @app.get("/metrics")
        async def metrics() -> dict:
            return {}

        @app.get("/health/liveness")
        async def liveness() -> dict:
            return {}

        client = TestClient(app)
        client.get("/metrics")
        assert fake_logger.calls == []
        client.get("/health/liveness")
        assert len(only(fake_logger.calls, "request_finished")) == 1


class TestOptionsRequests:
    def test_cors_preflight_not_logged(self, client, fake_logger):
        client.options("/items/1")
        assert only(fake_logger.calls, "request_finished") == []


class TestTemplateExclusion:
    def test_route_template_entry_excludes_only_that_route(self, fake_logger):
        app = FastAPI()
        add_logging_middleware(app, excluded_paths={"/health", "/task/{task_id}/status"})

        @app.get("/task/{task_id}/status")
        async def status(task_id: str) -> dict:
            return {}

        @app.get("/task/{task_id}/result")
        async def result(task_id: str) -> dict:
            return {}

        client = TestClient(app)
        client.get("/task/abc/status")
        assert fake_logger.calls == []
        client.get("/task/abc/result")
        finished = only(fake_logger.calls, "request_finished")
        assert len(finished) == 1
        assert finished[0][2]["path"] == "/task/{task_id}/result"
