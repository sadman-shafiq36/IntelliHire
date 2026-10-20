# the transcriber writes how each room's transcription is going to redis every few
# seconds; the api reads it for the interviewer's room page. a missing key means the
# transcriber isn't in the room

import json
from typing import Any

from app.logging_config import get_logger
from app.worker.queue import get_redis

log = get_logger(__name__)

TTL_SECONDS = 15
ALIVE_KEY = "transcriber:alive"


def _key(interview_id: int) -> str:
    return f"transcriber:room:{interview_id}"


def publish(interview_id: int, status: dict[str, Any]) -> None:
    get_redis().set(_key(interview_id), json.dumps(status), ex=TTL_SECONDS)


def clear(interview_id: int) -> None:
    get_redis().delete(_key(interview_id))


def heartbeat() -> None:
    get_redis().set(ALIVE_KEY, "1", ex=TTL_SECONDS)


def read(interview_id: int) -> dict[str, Any]:
    try:
        raw = get_redis().get(_key(interview_id))
    except Exception:
        log.warning("transcription_status_unavailable", interview_id=interview_id)
        return {"state": "unknown", "message": "Transcription status is not available."}
    if not raw:
        return {"state": "stopped", "message": "Transcription is not running."}
    return json.loads(raw)
