import logging
import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, TypedDict

import aiohttp
from fastapi import APIRouter, HTTPException, Response


class ServiceDependency(TypedDict):
    name: str
    health_check_url: str
    api_key: str | None


def health_probe_router(service_dependencies: list[ServiceDependency]) -> APIRouter:
    router = APIRouter(prefix="/health")

    START_TIME = time.time()

    # Disable logging for health check endpoints
    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            # Endpoints to exclude from logging
            skip_paths = {"/health"}

            # Extract the request path from the log message
            return all(skip_path not in record.getMessage() for skip_path in skip_paths)

    # Configure the filter
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

        health_check: dict[str, Any] = {
            "status": "ready",
            "checks": {service["name"]: "unknown" for service in service_dependencies},
        }

        try:
            timeout = aiohttp.ClientTimeout(total=5.0)

            for service in service_dependencies:
                async with aiohttp.ClientSession(
                    timeout=timeout,
                    headers={"Authorization": f"Bearer {service['api_key']}"} if service["api_key"] else {},
                ) as session:
                    try:
                        async with session.get(service["health_check_url"]) as svc_response:
                            if svc_response.status == 200:
                                health_check["checks"][service["name"]] = "healthy"
                            else:
                                health_check["checks"][service["name"]] = f"unhealthy (status: {svc_response.status})"
                                raise HTTPException(
                                    status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                                    detail=f"{service['name']} returned status {svc_response.status}",
                                )
                    except aiohttp.ClientError as e:
                        health_check["checks"][service["name"]] = f"error: {e!s}"
                        raise

        except Exception as e:
            # If a critical dependency fails, we must return a 503.
            # This tells K8s to stop sending traffic to this specific pod.
            response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
            return {"status": "unhealthy", "checks": health_check["checks"], "error": str(e)}
        else:
            return health_check

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
