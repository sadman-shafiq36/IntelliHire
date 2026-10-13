# every graph runs through run_graph so it gets an AgentRun audit row.
# prompts, replies and provider error text are never stored, only an error code

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.errors import ExternalServiceError
from app.logging_config import get_logger
from app.models.analysis import AgentRun
from app.models.enums import AgentRunStatus

log = get_logger(__name__)


def new_thread_id(prefix: str) -> str:
    # e.g. screening-3f9c0a1b2c3d
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def run_graph(
    db: Session,
    graph_name: str,
    thread_id: str,
    invoke: Callable[[], dict[str, Any]],
    context: dict[str, Any] | None = None,
    failure_code: Callable[[dict[str, Any]], str | None] | None = None,
) -> dict[str, Any]:
    # db is only used for the audit row, context must hold ids only ({"job_id": 3}).
    # failure_code marks a run failed when the graph finished without a usable result
    started = datetime.now(UTC)
    monotonic_start = time.monotonic()

    run = AgentRun(
        graph_name=graph_name,
        thread_id=thread_id,
        status=AgentRunStatus.RUNNING,
        started_at=started,
        context=context or {},
    )
    db.add(run)
    db.commit()

    log.info("agent_run_started", graph=graph_name, thread_id=thread_id)

    try:
        final_state = invoke()
    except Exception as exc:
        run.status = AgentRunStatus.FAILED
        run.error_code = type(exc).__name__
        run.finished_at = datetime.now(UTC)
        run.duration_ms = int((time.monotonic() - monotonic_start) * 1000)
        db.commit()

        log.exception(
            "agent_run_failed",
            graph=graph_name,
            thread_id=thread_id,
            error_type=type(exc).__name__,
        )
        raise ExternalServiceError(
            "The AI step could not be completed. Please try again in a moment.",
            {"graph": graph_name},
        ) from exc

    code = failure_code(final_state) if failure_code else None
    run.status = AgentRunStatus.FAILED if code else AgentRunStatus.SUCCEEDED
    run.error_code = code
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((time.monotonic() - monotonic_start) * 1000)
    db.commit()

    log.info(
        "agent_run_failed" if code else "agent_run_succeeded",
        graph=graph_name,
        thread_id=thread_id,
        duration_ms=run.duration_ms,
        error_count=len(final_state.get("errors", [])),
    )
    return final_state


def record_run(
    db: Session,
    graph_name: str,
    thread_id: str,
    started: datetime,
    duration_ms: int,
    error_code: str | None,
    context: dict[str, Any] | None = None,
) -> None:
    # for graphs run on worker threads: the audit row is written afterwards, on the
    # caller's own session, since sessions can't be shared between threads
    db.add(
        AgentRun(
            graph_name=graph_name,
            thread_id=thread_id,
            status=AgentRunStatus.FAILED if error_code else AgentRunStatus.SUCCEEDED,
            error_code=error_code,
            started_at=started,
            finished_at=started + timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
            context=context or {},
        )
    )
