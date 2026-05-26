import logging
import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TypedDict

import aiohttp
from fastapi import APIRouter, Response

logger = logging.getLogger(__name__)


class ServiceDependency(TypedDict):
    name: str
    health_check_url: str
    api_key: str | None


async def _check_service(session: aiohttp.ClientSession, service: ServiceDependency) -> str:
    headers = {"Authorization": f"Bearer {service['api_key']}"} if service["api_key"] else {}
    try:
        async with session.get(service["health_check_url"], headers=headers) as resp:
            if resp.status == 200:
                return "healthy"
            try:
                body = (await resp.text()).strip()
            except Exception:
                logger.exception(f"Cannot read response body for service={service['name']}")
                body = ""
            return f"unhealthy (status: {resp.status}){f': {body}' if body else ''}"
    except Exception as e:
        logger.error(f"Health check failed for {service['name']}: {e}", exc_info=True)
        return f"error: {e!s}"


def health_probe_router(service_dependencies: list[ServiceDependency]) -> APIRouter:
    router = APIRouter(prefix="/health")

    START_TIME = time.time()

    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "/health" not in record.getMessage()

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
        """
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            checks = {service["name"]: await _check_service(session, service) for service in service_dependencies}

        if all(v == "healthy" for v in checks.values()):
            return {"status": "ready", "checks": checks}

        logger.warning(f"Readiness probe unhealthy: {checks}")
        response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
        return {"status": "unhealthy", "checks": checks}

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
