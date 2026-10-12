# /health        - is the process alive (used by the docker healthcheck)
# /health/ready  - checks every dependency and reports each one

from typing import Any

import httpx
import redis
from fastapi import APIRouter
from sqlalchemy import text

from app.config import settings
from app.db.session import get_engine
from app.logging_config import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 3.0


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "intellihire-api", "environment": settings.app_env}


def _check_postgres() -> dict[str, Any]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        log.warning("dependency_unavailable", dependency="postgres", error_type=type(exc).__name__)
        return {"status": "unavailable"}


def _check_redis() -> dict[str, Any]:
    try:
        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=PROBE_TIMEOUT_SECONDS
        )
        client.ping()
        return {"status": "ok"}
    except Exception as exc:
        log.warning("dependency_unavailable", dependency="redis", error_type=type(exc).__name__)
        return {"status": "unavailable"}


def _check_chroma() -> dict[str, Any]:
    url = f"http://{settings.chroma_host}:{settings.chroma_port}/api/v2/heartbeat"
    try:
        response = httpx.get(url, timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
        return {"status": "ok"}
    except Exception as exc:
        log.warning("dependency_unavailable", dependency="chroma", error_type=type(exc).__name__)
        return {"status": "unavailable"}


def _check_whisper() -> dict[str, Any]:
    url = f"{settings.whisper_service_url.rstrip('/')}/health"
    try:
        response = httpx.get(url, timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
        return {"status": "ok", "model": response.json().get("model")}
    except Exception as exc:
        log.warning("dependency_unavailable", dependency="whisper", error_type=type(exc).__name__)
        return {"status": "unavailable"}


@router.get("/health/ready", summary="Readiness probe with dependency detail")
def readiness() -> dict[str, Any]:
    # always 200, status is "degraded" if anything is down
    dependencies = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "chroma": _check_chroma(),
        "whisper": _check_whisper(),
    }
    all_ok = all(dep["status"] == "ok" for dep in dependencies.values())

    # otherwise a missing llm key only shows up on the first agent run
    configuration = {
        "gemini_api_key_present": bool(settings.google_api_key),
        "jwt_secret_configured": bool(settings.jwt_secret_key),
    }

    return {
        "status": "ok" if all_ok else "degraded",
        "dependencies": dependencies,
        "configuration": configuration,
    }
