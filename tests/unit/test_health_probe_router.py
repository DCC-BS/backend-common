from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dcc_backend_common.fastapi_health_probes import router as router_module
from dcc_backend_common.fastapi_health_probes.router import (
    DependencyResult,
    ServiceDependency,
    _error_signature,
    health_probe_router,
)

DEPS: list[ServiceDependency] = [
    {"name": "svc-a", "health_check_url": "http://svc-a/health", "api_key": None},
    {"name": "svc-b", "health_check_url": "http://svc-b/health", "api_key": "k"},
]


class FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, **kwargs: object) -> None:
        self.calls.append((level, event, kwargs))

    def error(self, event: str, /, **kwargs: object) -> None:
        self._record("error", event, **kwargs)

    def warning(self, event: str, /, **kwargs: object) -> None:
        self._record("warning", event, **kwargs)

    def info(self, event: str, /, **kwargs: object) -> None:
        self._record("info", event, **kwargs)

    def exception(self, event: str, /, **kwargs: object) -> None:
        self._record("exception", event, **kwargs)


class Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class ScriptedChecks:
    """Returns scripted DependencyResults per dependency name, call by call."""

    def __init__(self, script: dict[str, list[DependencyResult]]) -> None:
        self.script = script
        self._i: dict[str, int] = {}

    async def __call__(self, service: dict, timeout: object) -> DependencyResult:
        name = service["name"]
        index = self._i.get(name, 0)
        self._i[name] = index + 1
        results = self.script[name]
        # Past the last scripted result, keep repeating the final state.
        return results[index] if index < len(results) else results[-1]


def ok(name: str) -> DependencyResult:
    return DependencyResult(name=name, healthy=True, signature=None, detail="healthy")


def fail(name: str, signature: str, detail: str = "boom") -> DependencyResult:
    return DependencyResult(name=name, healthy=False, signature=signature, detail=detail)


def levels(calls: list[tuple[str, str, dict]]) -> list[str]:
    return [c[0] for c in calls]


@pytest.fixture
def harness(monkeypatch):
    """Wire a fake logger, controllable clock, and scripted checks into the module."""
    fake_logger = FakeLogger()
    clock = Clock()
    monkeypatch.setattr(router_module, "logger", fake_logger)
    monkeypatch.setattr(router_module, "_monotonic", clock)

    def build_app(script: dict[str, list[DependencyResult]]) -> TestClient:
        monkeypatch.setattr(router_module, "_check_dependency", ScriptedChecks(script))
        app = FastAPI()
        app.include_router(health_probe_router(DEPS))
        return TestClient(app)

    return SimpleNamespace(logger=fake_logger, clock=clock, build_app=build_app)


def test_error_signature_is_stable_and_excludes_messages():
    assert _error_signature(200, None) is None
    assert _error_signature(None, None) is None
    assert _error_signature(503, None) == "http:503"
    assert _error_signature(500, None) == "http:500"

    class FooClientError(Exception):
        pass

    assert _error_signature(None, FooClientError("volatile text 123ms")) == "FooClientError"


def test_first_failure_logged_then_suppressed_until_recovery(harness):
    client = harness.build_app({
        "svc-a": [fail("svc-a", "http:503"), fail("svc-a", "http:503"), fail("svc-a", "http:503"), ok("svc-a")],
        "svc-b": [ok("svc-b")],
    })

    r1 = client.get("/health/readiness")  # first occurrence
    assert r1.status_code == 503
    assert levels(harness.logger.calls) == ["error"]
    assert harness.logger.calls[0][2]["service"] == "svc-a"
    assert harness.logger.calls[0][2]["signature"] == "http:503"

    client.get("/health/readiness")  # suppressed
    client.get("/health/readiness")  # suppressed
    assert levels(harness.logger.calls) == ["error"]  # still only the first occurrence

    r4 = client.get("/health/readiness")  # recovery
    assert levels(harness.logger.calls) == ["error", "info"]
    recovery = harness.logger.calls[1][2]
    assert recovery["service"] == "svc-a"
    assert recovery["previous_signature"] == "http:503"
    assert recovery["suppressed_probe_count"] == 2  # two retries suppressed
    assert r4.status_code == 200


def test_repeated_failures_stay_silent(harness):
    client = harness.build_app(
        {
            "svc-a": [fail("svc-a", "http:503")] * 4,
            "svc-b": [ok("svc-b")],
        },
    )

    client.get("/health/readiness")  # first occurrence (error)
    harness.clock.advance(5)
    client.get("/health/readiness")  # suppressed
    assert levels(harness.logger.calls) == ["error"]

    harness.clock.advance(6)
    client.get("/health/readiness")  # still suppressed
    assert levels(harness.logger.calls) == ["error"]

    harness.clock.advance(3)
    client.get("/health/readiness")  # still suppressed
    assert levels(harness.logger.calls) == ["error"]


def test_signature_change_logs_new_first_occurrence(harness):
    client = harness.build_app({
        "svc-a": [fail("svc-a", "http:503"), fail("svc-a", "ClientConnectorError", detail="error: refused")],
        "svc-b": [ok("svc-b")],
    })

    client.get("/health/readiness")
    client.get("/health/readiness")

    assert levels(harness.logger.calls) == ["error", "error"]
    assert harness.logger.calls[1][2]["signature"] == "ClientConnectorError"


def test_recovery_resets_state_so_next_failure_logs_again(harness):
    client = harness.build_app({
        "svc-a": [fail("svc-a", "http:503"), ok("svc-a"), fail("svc-a", "http:503")],
        "svc-b": [ok("svc-b")],
    })

    client.get("/health/readiness")  # error #1
    client.get("/health/readiness")  # recovery (info)
    client.get("/health/readiness")  # new outage -> error #2

    assert levels(harness.logger.calls) == ["error", "info", "error"]
    assert harness.logger.calls[2][2]["signature"] == "http:503"


def test_all_dependencies_checked_each_probe_no_short_circuit(harness):
    client = harness.build_app({
        "svc-a": [fail("svc-a", "http:503", detail="status 503")],
        "svc-b": [fail("svc-b", "http:500", detail="status 500: nope")],
    })

    resp = client.get("/health/readiness")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["svc-a"] == "status 503"
    assert body["checks"]["svc-b"] == "status 500: nope"
    assert "svc-a" in body["error"] and "svc-b" in body["error"]
    # Both dependencies get their own First Occurrence log.
    services = {c[2]["service"] for c in harness.logger.calls}
    assert services == {"svc-a", "svc-b"}


def test_all_healthy_is_silent(harness):
    client = harness.build_app({
        "svc-a": [ok("svc-a")] * 3,
        "svc-b": [ok("svc-b")] * 3,
    })

    for _ in range(3):
        resp = client.get("/health/readiness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    assert harness.logger.calls == []


def test_liveness_and_startup_probes(harness):
    client = harness.build_app({"svc-a": [ok("svc-a")], "svc-b": [ok("svc-b")]})

    live = client.get("/health/liveness")
    assert live.status_code == 200
    assert live.json()["status"] == "up"
    assert "uptime_seconds" in live.json()

    startup = client.get("/health/startup")
    assert startup.status_code == 200
    assert startup.json()["status"] == "started"
    assert harness.logger.calls == []
