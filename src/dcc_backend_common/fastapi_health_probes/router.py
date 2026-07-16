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
    """Stable key identifying the current failure mode (see ``_error_signature``).

    ``None`` means the dependency is currently healthy; a change between non-None
    values triggers a fresh First Occurrence log.
    """

    first_failure_at: float | None = None
    """Monotonic timestamp of the first probe that exhibited the current
    ``signature``. Used to report outage duration on recovery. Reset to ``None``
    whenever the dependency is healthy or the signature changes.
    """

    last_failure_at: float | None = None
    """Monotonic timestamp of the most recent failing probe. Updated on every
    failure so a later recovery can report the last seen time.
    """

    last_error: str = ""
    """Human-readable detail string from the most recent failing probe. Surfaced
    in the Recovery Summary so operators see the last error before recovery.
    """

    suppressed_count: int = 0
    """Number of consecutive failing probes suppressed since the First Occurrence
    for the current ``signature``. Reported in the Recovery Summary and reset to
    zero on signature change or recovery.
    """


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


def _apply_state(state: _DependencyHealthState, result: DependencyResult) -> None:
    """
    Advance the per-dependency state machine and emit the appropriate log.

    - healthy -> was unhealthy: emit Recovery Summary (INFO), reset state.
    - failing -> was healthy: emit First Occurrence (ERROR), start an Outage.
    - failing -> same signature: Suppressed Probe; stay silent.
    - failing -> signature changed: emit a fresh First Occurrence (ERROR) for
      the new signature.
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
        return

    elif state.signature is None:
        state.signature = result.signature
        state.first_failure_at = now
        state.last_failure_at = now
        state.last_error = result.detail
        state.suppressed_count = 0
        logger.error(
            "health check failed",
            service=result.name,
            signature=result.signature,
            detail=result.detail,
        )
        return
    else:
        state.last_failure_at = now
        state.last_error = result.detail
        state.suppressed_count += 1

        if state.signature == result.signature:
            return

        state.signature = result.signature
        state.first_failure_at = now
        state.last_failure_at = now
        state.last_error = result.detail
        state.suppressed_count = 0
        logger.error(
            "health check failed",
            service=result.name,
            signature=result.signature,
            detail=result.detail,
        )


def health_probe_router(
    service_dependencies: list[ServiceDependency],
) -> APIRouter:
    """
    Build the health-probe router.

    Args:
        service_dependencies: Downstream services to check from the readiness probe.
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
        First Occurrence is logged in full, retries are Suppressed, and a Recovery
        Summary is emitted when the dependency comes back.
        """
        timeout = aiohttp.ClientTimeout(total=5.0)

        # Probe every dependency (no short-circuit) so each one's state advances.
        results = [await _check_dependency(service, timeout) for service in service_dependencies]

        health_checks: dict[str, str] = {}
        async with state_lock:
            for result in results:
                health_checks[result.name] = "healthy" if result.healthy else (result.detail or "unhealthy")
                state = states.setdefault(result.name, _DependencyHealthState())
                _apply_state(state, result)

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
