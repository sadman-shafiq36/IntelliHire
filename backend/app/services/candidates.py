# deleting a candidate and everything derived from them: database rows, cv files on
# disk, and the agent checkpoints, which hold the cv text the graphs worked on

from collections.abc import Callable, Iterable
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.errors import ConflictError, ExternalServiceError, NotFoundError
from app.logging_config import get_logger
from app.models.analysis import AgentRun, Assessment, InterviewReview
from app.models.candidate import Candidate, CvDocument
from app.models.enums import InterviewStatus
from app.models.feedback import CandidateFeedback
from app.models.identity import IdentityCheck
from app.models.interview import Interview
from app.models.screening import CandidateScore, Screening
from app.services import identity

log = get_logger(__name__)

CheckpointDeleter = Callable[[list[str]], None]


def _delete_checkpoints(thread_ids: list[str]) -> None:
    from app.agents.checkpointer import graph_checkpointer

    with graph_checkpointer() as saver:
        for thread_id in thread_ids:
            saver.delete_thread(thread_id)


def _thread_ids(db: Session, candidate_id: int, doc_ids: Iterable[int]) -> list[str]:
    docs = set(doc_ids)
    # every graph that sees a cv or a candidate records one of these ids
    runs = db.scalars(select(AgentRun))
    found = []
    for run in runs:
        ctx = run.context or {}
        if ctx.get("cv_document_id") in docs or ctx.get("candidate_id") == candidate_id:
            found.append(run.thread_id)
    return sorted(set(found))


def delete_candidate(
    db: Session, candidate_id: int, delete_checkpoints: CheckpointDeleter | None = None
) -> dict[str, int]:
    from app.services.scoring import rank_scores
    from app.services.screening import rank_job

    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise NotFoundError("That candidate does not exist or has already been deleted.")
    docs = db.scalars(select(CvDocument).where(CvDocument.candidate_id == candidate_id)).all()
    files = [Path(d.storage_path) for d in docs]
    # feedback emails saved locally when the email service couldn't be reached
    files += [
        Path(path)
        for path in db.scalars(
            select(CandidateFeedback.outbox_path).where(
                CandidateFeedback.candidate_id == candidate_id,
                CandidateFeedback.outbox_path.is_not(None),
            )
        )
    ]
    threads = _thread_ids(db, candidate_id, [d.id for d in docs])
    jobs = {
        *db.scalars(select(Screening.job_id).where(Screening.candidate_id == candidate_id)),
        *db.scalars(
            select(CandidateScore.job_id).where(CandidateScore.candidate_id == candidate_id)
        ),
    }

    upcoming = db.scalars(
        select(Interview).where(
            Interview.candidate_id == candidate_id,
            Interview.status.in_(
                [
                    InterviewStatus.REQUESTED,
                    InterviewStatus.SCHEDULED,
                    InterviewStatus.IN_PROGRESS,
                ]
            ),
        )
    ).first()
    if upcoming is not None:
        # otherwise the calendar event and the candidate's link would outlive the record,
        # and a live interview would keep writing transcript lines for nobody
        if upcoming.status == InterviewStatus.IN_PROGRESS:
            raise ConflictError("End this candidate's interview first, then delete their data.")
        raise ConflictError("Cancel this candidate's interview first, then delete their data.")

    # checkpoints first: if they can't be removed, nothing is, and the request can be retried
    try:
        (delete_checkpoints or _delete_checkpoints)(threads)
    except Exception as exc:
        log.exception("candidate_delete_failed", candidate_id=candidate_id, step="checkpoints")
        raise ExternalServiceError(
            "The candidate could not be deleted completely. Nothing was removed; try again."
        ) from exc

    for row in [
        *db.scalars(select(Screening).where(Screening.candidate_id == candidate_id)),
        *db.scalars(select(CandidateScore).where(CandidateScore.candidate_id == candidate_id)),
    ]:
        db.delete(row)
    # interview data cascades from these in the database
    db.execute(delete(Assessment).where(Assessment.candidate_id == candidate_id))
    db.execute(delete(CandidateFeedback).where(CandidateFeedback.candidate_id == candidate_id))
    theirs = select(Interview.id).where(Interview.candidate_id == candidate_id)
    db.execute(delete(InterviewReview).where(InterviewReview.interview_id.in_(theirs)))
    db.execute(delete(IdentityCheck).where(IdentityCheck.interview_id.in_(theirs)))
    db.execute(delete(Interview).where(Interview.candidate_id == candidate_id))
    # a form photo still waiting in google drive goes too
    identity.delete_for_candidate(db, candidate_id, identity.drive_deleter(db))
    # documents, extractions and applications go with the candidate
    db.delete(candidate)
    db.commit()

    removed = 0
    for path in files:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            log.warning("cv_file_not_removed", candidate_id=candidate_id)
    for job_id in jobs:
        rank_job(db, job_id)
        rank_scores(db, job_id)

    log.info(
        "candidate_deleted",
        candidate_id=candidate_id,
        documents=len(docs),
        files=removed,
        checkpoints=len(threads),
    )
    return {"documents": len(docs), "files": removed, "checkpoints": len(threads)}
