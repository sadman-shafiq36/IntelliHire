# functions the worker runs. each opens its own db session. progress is only
# reported when running under rq, so tests can call these directly

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from rq import get_current_job

from app.db.session import get_session_factory
from app.errors import AppError
from app.logging_config import get_logger
from app.worker.queue import report_progress

log = get_logger(__name__)


def _progress_reporter(kind: str) -> Callable[..., None]:
    job = get_current_job()
    if job is None:
        return lambda **_: None

    def report(**kwargs: Any) -> None:
        report_progress(job.id, kind, **kwargs)

    return report


def _run(kind: str, work: Callable[[Callable[..., None]], dict[str, Any]]) -> dict[str, Any]:
    report = _progress_reporter(kind)
    report(state="running", message="Started.")
    try:
        result_ref = work(report)
    except AppError as exc:
        report(state="failed", message=exc.message)
        raise
    except Exception:
        report(state="failed", message="Something went wrong on our side. Please try again.")
        raise
    report(state="finished", message="Done.", total=1, completed=1, result_ref=result_ref)
    return result_ref


def index_policy_task(policy_id: int) -> dict[str, Any]:
    from app.models.enums import PolicyStatus
    from app.services.policies import index_policy

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db:
            policy = index_policy(db, policy_id, progress=report)
            if policy.status == PolicyStatus.FAILED:
                raise AppError(policy.failure_reason or "The policy could not be indexed.")
            return {"policy_id": policy.id, "chunk_count": policy.chunk_count}

    return _run("policy_indexing", work)


@contextmanager
def _jd_graph() -> Iterator[Any]:
    # checkpointed in postgres, tests swap this out for an in-memory graph
    from app.agents.checkpointer import graph_checkpointer
    from app.agents.jd.graph import build_jd_graph

    with graph_checkpointer() as saver:
        yield build_jd_graph(checkpointer=saver)


def generate_job_description_task(job_id: int) -> dict[str, Any]:
    from app.services.job_descriptions import generate_description

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _jd_graph() as graph:
            return generate_description(db, job_id, graph, progress=report)

    return _run("job_description", work)


def revise_job_description_task(job_id: int, message_id: int) -> dict[str, Any]:
    from app.services.job_descriptions import revise_description

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _jd_graph() as graph:
            return revise_description(db, job_id, message_id, graph, progress=report)

    return _run("job_description_chat", work)


def prepare_job_post_task(post_id: int) -> dict[str, Any]:
    from app.services.job_posting import prepare_post

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db:
            post = prepare_post(db, post_id, progress=report)
            return {"post_id": post.id, "job_id": post.job_id}

    return _run("job_post", work)


def sync_applications_task(job_id: int) -> dict[str, Any]:
    from app.services.applications import sync_applications

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db:
            return sync_applications(db, job_id, progress=report)

    return _run("application_sync", work)
