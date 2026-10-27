# flow: endpoint enqueues and returns 202 + job_id, worker calls report_progress,
# browser polls GET /api/v1/background-jobs/{job_id} until finished or failed

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import redis
from rq import Queue

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

QUEUE_NAME = "intellihire"

PROGRESS_TTL_SECONDS = 24 * 60 * 60

# a 20 cv batch with retries is the worst case, this sits well above it
JOB_TIMEOUT_SECONDS = 30 * 60


def get_redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis(), default_timeout=JOB_TIMEOUT_SECONDS)


@dataclass
class JobProgress:
    # failed_items = items skipped without aborting the whole run
    job_id: str
    kind: str
    state: str = "queued"  # queued | running | finished | failed
    message: str = ""
    total: int = 0
    completed: int = 0
    failed_items: list[dict[str, Any]] = field(default_factory=list)
    result_ref: dict[str, Any] | None = None
    updated_at: str = ""

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 100 if self.state == "finished" else 0
        return min(100, round(self.completed / self.total * 100))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["percent"] = self.percent
        return payload


def _progress_key(job_id: str) -> str:
    return f"job:progress:{job_id}"


def write_progress(progress: JobProgress) -> None:
    progress.updated_at = datetime.now(UTC).isoformat()
    get_redis().setex(
        _progress_key(progress.job_id),
        PROGRESS_TTL_SECONDS,
        json.dumps(progress.to_dict()),
    )


def read_progress(job_id: str) -> dict[str, Any] | None:
    raw = get_redis().get(_progress_key(job_id))
    if raw is None:
        return None
    return json.loads(raw)


def report_progress(
    job_id: str,
    kind: str,
    *,
    state: str,
    message: str = "",
    total: int = 0,
    completed: int = 0,
    failed_items: list[dict[str, Any]] | None = None,
    result_ref: dict[str, Any] | None = None,
) -> None:
    progress = JobProgress(
        job_id=job_id,
        kind=kind,
        state=state,
        message=message,
        total=total,
        completed=completed,
        failed_items=failed_items or [],
        result_ref=result_ref,
    )
    write_progress(progress)
    log.info(
        "job_progress",
        job_id=job_id,
        kind=kind,
        state=state,
        completed=completed,
        total=total,
    )


def enqueue(func_path: str, kind: str, **kwargs: Any) -> str:
    # dotted path instead of a function so the api doesn't import worker deps.
    # kwargs go in explicitly, rq reserves names like job_id for itself
    job = get_queue().enqueue(func_path, kwargs=kwargs)
    report_progress(job.id, kind, state="queued", message="Please wait.")
    log.info("job_enqueued", job_id=job.id, kind=kind, func=func_path)
    return job.id
