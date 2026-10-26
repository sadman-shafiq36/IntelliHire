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


def auto_sync_applications_task() -> dict[str, Any]:
    # queued by the worker's timer, so nobody polls its progress
    from app.services.applications import sync_live_jobs

    with get_session_factory()() as db:
        return sync_live_jobs(db)


@contextmanager
def _screening_graph() -> Iterator[Any]:
    # several cvs run at once, so the checkpointer needs its own connection pool
    from app.agents.checkpointer import pooled_checkpointer
    from app.agents.screening.graph import build_screening_graph
    from app.config import settings

    with pooled_checkpointer(max_size=settings.screening_concurrency + 1) as saver:
        yield build_screening_graph(checkpointer=saver)


def screen_job_task(job_id: int) -> dict[str, Any]:
    from app.services.screening import screen_job

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _screening_graph() as graph:
            return screen_job(db, job_id, graph, progress=report)

    return _run("cv_screening", work)


@contextmanager
def _scoring_graph() -> Iterator[Any]:
    from app.agents.checkpointer import pooled_checkpointer
    from app.agents.scoring.graph import build_scoring_graph
    from app.config import settings

    with pooled_checkpointer(max_size=settings.screening_concurrency + 1) as saver:
        yield build_scoring_graph(checkpointer=saver)


def _weights_proposer() -> Callable[[dict[str, Any]], tuple[dict[str, Any], str]]:
    # tests swap this for a fake, generate_structured has no generic one
    from app.agents.scoring.llm import llm_proposer

    return llm_proposer


def score_job_task(job_id: int, rescore: bool = False) -> dict[str, Any]:
    from app.services.scoring import score_job

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _scoring_graph() as graph:
            return score_job(
                db, job_id, graph, _weights_proposer(), rescore=rescore, progress=report
            )

    return _run("candidate_scoring", work)


def suggest_score_weights_task(job_id: int) -> dict[str, Any]:
    from app.services.scoring import resuggest_weights

    def work(report: Callable[..., None]) -> dict[str, Any]:
        report(state="running", message="Asking the AI for weights.")
        with get_session_factory()() as db:
            return resuggest_weights(db, job_id, _weights_proposer())

    return _run("score_weights", work)


@contextmanager
def _copilot_graph() -> Iterator[Any]:
    # tests swap this for a graph with a fake suggester
    from app.agents.checkpointer import graph_checkpointer
    from app.agents.copilot.graph import build_copilot_graph

    with graph_checkpointer() as saver:
        yield build_copilot_graph(checkpointer=saver)


def suggest_interview_questions_task(interview_id: int, focus: str | None = None) -> dict:
    from app.services.copilot import suggest

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _copilot_graph() as graph:
            return suggest(db, interview_id, focus, graph, progress=report)

    return _run("copilot_suggestions", work)


@contextmanager
def _analysis_graph() -> Iterator[Any]:
    # tests swap this for a graph with fake ai steps
    from app.agents.analysis.graph import build_analysis_graph
    from app.agents.checkpointer import graph_checkpointer

    with graph_checkpointer() as saver:
        yield build_analysis_graph(checkpointer=saver)


def analyse_interview_task(interview_id: int, wait_for_transcript: bool = True) -> dict:
    from app.services.interview_analysis import analyse

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _analysis_graph() as graph:
            return analyse(db, interview_id, graph, progress=report, wait=wait_for_transcript)

    return _run("interview_analysis", work)


@contextmanager
def _assessment_graph() -> Iterator[Any]:
    # tests swap this for a graph with a fake writer
    from app.agents.assessment.graph import build_assessment_graph
    from app.agents.checkpointer import graph_checkpointer

    with graph_checkpointer() as saver:
        yield build_assessment_graph(checkpointer=saver)


def assess_candidate_task(interview_id: int) -> dict:
    from app.services.assessment import assess

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _assessment_graph() as graph:
            return assess(db, interview_id, graph, progress=report)

    return _run("candidate_assessment", work)


@contextmanager
def _scheduling_runtime() -> Iterator[tuple[Any, Any]]:
    # yields (make_graph, make_tools). tests swap this for fakes: no mcp server, no llm
    from app.agents.checkpointer import graph_checkpointer
    from app.agents.scheduling.graph import build_scheduling_graph
    from app.agents.scheduling.messages import Signature
    from app.config import settings
    from app.tools.mcp_client import McpTools

    with graph_checkpointer() as saver:

        def make_graph(tools: Any, contact: Any) -> Any:
            return build_scheduling_graph(
                tools,
                contact,
                checkpointer=saver,
                work_start=settings.scheduling_work_start_hour,
                work_end=settings.scheduling_work_end_hour,
                search_days=settings.scheduling_search_days,
                signature=Signature(
                    settings.email_sender_name,
                    settings.email_sender_title,
                    settings.email_sender_company,
                ),
            )

        yield make_graph, McpTools


def schedule_interview_task(interview_id: int, action: str, options: dict[str, Any]) -> dict:
    from app.services.interviews import run_action

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _scheduling_runtime() as (make_graph, make_tools):
            return run_action(
                db, interview_id, action, options, make_graph, make_tools, progress=report
            )

    return _run("interview_scheduling", work)


@contextmanager
def _review_graph(kind: str) -> Iterator[Any]:
    # tests swap this for a graph with fake ai steps
    from app.agents.checkpointer import graph_checkpointer
    from app.agents.claims.graph import build_claims_graph
    from app.agents.interview_quality.graph import build_quality_graph
    from app.agents.interview_score.graph import build_interview_score_graph

    builders = {
        "claims": build_claims_graph,
        "interview_score": build_interview_score_graph,
        "quality": build_quality_graph,
    }
    with graph_checkpointer() as saver:
        yield builders[kind](checkpointer=saver)


def review_interview_task(interview_id: int, review_kind: str) -> dict:
    # not "kind": enqueue takes that for the background job's kind
    from app.services.interview_reviews import KINDS, review

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _review_graph(review_kind) as graph:
            return review(db, interview_id, review_kind, graph, progress=report)

    return _run(KINDS[review_kind].job_kind, work)


def prepare_face_reference_task(candidate_id: int, interview_id: int | None = None) -> dict:
    # an interview was booked: the applicant's form photo becomes an encrypted template
    from app.models.interview import Interview
    from app.services import identity

    def work(report: Callable[..., None]) -> dict[str, Any]:
        report(state="running", message="Preparing the face reference.")
        with get_session_factory()() as db:
            interview = db.get(Interview, interview_id) if interview_id else None
            status = identity.prepare_reference(
                db,
                candidate_id,
                identity.drive_downloader(db),
                identity.drive_deleter(db) or (lambda file_id: None),
                keep_from=interview.scheduled_end if interview else None,
            )
            return {"candidate_id": candidate_id, "status": status}

    return _run("face_reference", work)


def biometric_sweep_task() -> dict:
    from app.services import identity

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db:
            return {"removed": identity.sweep(db, identity.drive_deleter(db))}

    return _run("biometric_sweep", work)


@contextmanager
def _feedback_graph() -> Iterator[Any]:
    # human review interrupts the graph, so it must resume from the postgres checkpoints.
    # tests swap this for a graph with fake ai steps and an in-memory saver
    from app.agents.checkpointer import graph_checkpointer
    from app.agents.feedback.graph import build_feedback_graph

    with graph_checkpointer() as saver:
        yield build_feedback_graph(checkpointer=saver)


def compose_feedback_task(feedback_id: int, resume: bool = False) -> dict:
    from app.services.candidate_feedback import run

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _feedback_graph() as graph:
            return run(db, feedback_id, graph, resume=resume, progress=report)

    return _run("candidate_feedback", work)


def compose_feedback_batch_task(feedback_ids: list[int]) -> dict:
    from app.services.candidate_feedback import run_batch

    def work(report: Callable[..., None]) -> dict[str, Any]:
        with get_session_factory()() as db, _feedback_graph() as graph:
            return run_batch(db, feedback_ids, graph, progress=report)

    return _run("candidate_feedback_batch", work)
