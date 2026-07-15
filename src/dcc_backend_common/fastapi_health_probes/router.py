import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TypedDict

import aiohttp
from fastapi import APIRouter, Response

from dcc_backend_common.logger import get_logger

logger = get_logger(__name__)

# Indirection so tests can advance the clock deterministically.
_monotonic = time.monotonic


class ServiceDependency(TypedDict):
    name: str
    health_check_url: str
    api_key: str | None


@dataclass(slots=True)
class DependencyResult:
    """Outcome of probing a single Service Dependency."""

    name: str
    healthy: bool
    signature: str | None
    detail: str


@dataclass(slots=True)
class _DependencyHealthState:
    """Per-dependency dedup state, kept only for the lifetime of the process."""

    signature: str | None = None
    first_failure_at: float | None = None
    last_failure_at: float | None = None
    last_error: str = ""
    suppressed_count: int = 0
    since_heartbeat_count: int = 0
    last_heartbeat_at: float | None = None


def _error_signature(status: int | None, exc: BaseException | None) -> str | None:
    """
    Stable key that tells one failure apart from another.

    Returns ``None`` for a healthy result, ``http:{status}`` for a non-200
    response, or ``{ExceptionClassName}`` for a transport/connection error. The
    volatile message text is deliberately excluded so that flapping messages
    (e.g. a timeout duration that changes every call) do not defeat suppression.
    """
    if exc is not None:
        return type(exc).__name__
    if status is not None and status != 200:
        return f"http:{status}"
    return None


async def _check_dependency(service: ServiceDependency, timeout: aiohttp.ClientTimeout) -> DependencyResult:
    """
    Probe a single Service Dependency.

    Deliberately side-effect free: it never logs. All logging decisions are made
    by the readiness state machine, which knows whether a failure is a First
    Occurrence, a Suppressed Probe, or a Heartbeat.
    """
    name = service["name"]
    headers = {"Authorization": f"Bearer {service['api_key']}"} if service["api_key"] else {}
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout, headers=headers) as session,
            session.get(service["health_check_url"]) as svc_response,
        ):
            status = svc_response.status
            if status == 200:
                return DependencyResult(name=name, healthy=True, signature=None, detail="healthy")
            body = ""
            try:
                body = (await svc_response.text()).strip()
            except Exception:
                logger.exception("cannot read health-check response body", service=name)
            detail = f"status {status}" + (f": {body}" if body else "")
            return DependencyResult(
                name=name,
                healthy=False,
                signature=_error_signature(status, None),
                detail=detail,
            )
    except aiohttp.ClientError as e:
        return DependencyResult(name=name, healthy=False, signature=_error_signature(None, e), detail=f"error: {e!s}")


def _apply_state(state: _DependencyHealthState, result: DependencyResult, heartbeat_interval_s: float) -> None:
    """
    Advance the per-dependency state machine and emit the appropriate log.

    - healthy -> was unhealthy: emit Recovery Summary (INFO), reset state.
    - failing -> was healthy: emit First Occurrence (ERROR), start an Outage.
    - failing -> same signature: Suppressed Probe; emit Heartbeat (WARNING) on
      the interval, otherwise stay silent.
    - failing -> signature changed: emit a transition (WARNING) then a fresh
      First Occurrence (ERROR) for the new signature.
    """
    now = _monotonic()

    if result.healthy:
        if state.signature is not None:
            logger.info(
                "health check recovered",
                service=result.name,
                previous_signature=state.signature,
                outage_duration_s=round(now - state.first_failure_at, 3) if state.first_failure_at else None,
                suppressed_probe_count=state.suppressed_count,
                last_error=state.last_error,
            )
        state.signature = None
        state.first_failure_at = None
        state.last_failure_at = None
        state.last_error = ""
        state.suppressed_count = 0
        state.since_heartbeat_count = 0
        state.last_heartbeat_at = None
        return

    if state.signature is None:
        state.signature = result.signature
        state.first_failure_at = now
        state.last_failure_at = now
        state.last_error = result.detail
        state.suppressed_count = 0
        state.since_heartbeat_count = 0
        state.last_heartbeat_at = now
        logger.error(
            "health check failed",
            service=result.name,
            signature=result.signature,
            detail=result.detail,
        )
        return

    state.last_failure_at = now
    state.last_error = result.detail
    state.suppressed_count += 1
    state.since_heartbeat_count += 1

    if state.signature == result.signature:
        if state.last_heartbeat_at is not None and now - state.last_heartbeat_at >= heartbeat_interval_s:
            logger.warning(
                "health check still failing",
                service=result.name,
                signature=state.signature,
                outage_duration_s=round(now - state.first_failure_at, 3) if state.first_failure_at else None,
                suppressed_probe_count=state.suppressed_count,
                probes_since_last_heartbeat=state.since_heartbeat_count,
                last_error=state.last_error,
            )
            state.last_heartbeat_at = now
            state.since_heartbeat_count = 0
        return

    logger.warning(
        "health check failure mode changed",
        service=result.name,
        previous_signature=state.signature,
        new_signature=result.signature,
        outage_duration_s=round(now - state.first_failure_at, 3) if state.first_failure_at else None,
        suppressed_probe_count=state.suppressed_count,
        last_error=state.last_error,
    )
    state.signature = result.signature
    state.first_failure_at = now
    state.last_failure_at = now
    state.last_error = result.detail
    state.suppressed_count = 0
    state.since_heartbeat_count = 0
    state.last_heartbeat_at = now
    logger.error(
        "health check failed",
        service=result.name,
        signature=result.signature,
        detail=result.detail,
    )


def health_probe_router(
    service_dependencies: list[ServiceDependency],
    *,
    heartbeat_interval_s: float = 600.0,
) -> APIRouter:
    """
    Build the health-probe router.

    Args:
        service_dependencies: Downstream services to check from the readiness probe.
        heartbeat_interval_s: While a dependency stays unhealthy, emit a "still
            failing" Heartbeat summary log this often (seconds). Default 10 minutes.
    """
    router = APIRouter(prefix="/health")

    START_TIME = time.time()

    # Dedup state and its guard. Per-process: each pod deduplicates its own probes.
    states: dict[str, _DependencyHealthState] = {}
    state_lock = asyncio.Lock()

    # Disable access logging for health check endpoints.
    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            skip_paths = {"/health"}
            return all(skip_path not in record.getMessage() for skip_path in skip_paths)

    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

    @router.get("/liveness")
    async def liveness_probe():
        """
        Liveness Probe
        * Purpose: Checks if the application process is running and not deadlocked.
        * K8s Action: If this fails, the container is KILLED and RESTARTED.
        * Rule: Keep it simple. Do NOT check databases here.
        """
        return {"status": "up", "uptime_seconds": time.time() - START_TIME}

    @router.get("/readiness")
    async def readiness_probe(response: Response):
        """
        Readiness Probe
        * Purpose: Checks if the app is ready to handle user requests (e.g., external APIs).
        * K8s Action: If this fails, traffic stops sending to this pod.
        * Rule: Check critical dependencies here.

        Failure logs are deduplicated per (dependency, Error Signature): only the
        First Occurrence is logged in full, retries are Suppressed, and a Heartbeat
        plus Recovery Summary keep long Outages visible without spamming.
        """
        timeout = aiohttp.ClientTimeout(total=5.0)

        # Probe every dependency (no short-circuit) so each one's state advances.
        results = [await _check_dependency(service, timeout) for service in service_dependencies]

        health_checks: dict[str, str] = {}
        async with state_lock:
            for result in results:
                health_checks[result.name] = "healthy" if result.healthy else (result.detail or "unhealthy")
                state = states.setdefault(result.name, _DependencyHealthState())
                _apply_state(state, result, heartbeat_interval_s)

        unhealthy = [r for r in results if not r.healthy]
        if unhealthy:
            response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
            return {
                "status": "unhealthy",
                "checks": health_checks,
                "error": "; ".join(f"{r.name}: {r.detail}" for r in unhealthy),
            }
        return {"status": "ready", "checks": health_checks}

    @router.get("/startup")
    async def startup_probe():
        """
        Startup Probe
        * Purpose: Checks if the application has finished initialization.
        * K8s Action: Blocks Liveness/Readiness probes until this returns 200.
        * Rule: Useful for apps that need to load large ML models or caches on boot.
        """
        return {"status": "started", "timestamp": datetime.now(UTC).isoformat()}

    return router
