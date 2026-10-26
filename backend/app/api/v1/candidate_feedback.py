from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import candidate_feedback as service

router = APIRouter(tags=["candidate feedback"])


class Note(BaseModel):
    author: Literal["interviewer", "recruiter"] = "recruiter"
    text: str = Field(min_length=1, max_length=service.MAX_NOTE_CHARS)


class GenerateRequest(BaseModel):
    job_id: int
    stage: Literal["screening", "interview"]
    outcome: Literal["not_progressing", "on_hold", "next_round"] | None = None
    interview_id: int | None = None
    # leave out to keep the notes already saved with this feedback
    notes: list[Note] | None = Field(default=None, max_length=service.MAX_NOTES)


class Started(BaseModel):
    feedback_id: int
    background_job_id: str


class BatchStarted(BaseModel):
    background_job_id: str
    candidate_count: int


class MarkRequest(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=200)


class Marked(BaseModel):
    marked: int


class ReviewRequest(BaseModel):
    action: Literal["approve", "edit", "regenerate", "discard"]
    subject: str | None = Field(default=None, max_length=service.MAX_SUBJECT)
    body: str | None = Field(default=None, max_length=service.MAX_BODY)
    instruction: str | None = Field(default=None, max_length=300)


class ReviewResult(BaseModel):
    status: str
    background_job_id: str | None


class Summary(BaseModel):
    id: int
    candidate_id: int
    candidate_name: str
    job_id: int
    interview_id: int | None
    stage: str
    outcome: str
    status: str
    message: str | None
    fallback_reason: str | None
    guardrail_passed: bool
    marked_at: datetime | None
    approved_at: datetime | None
    sent_at: datetime | None
    send_channel: str | None
    updated_at: datetime | None


class Detail(Summary):
    job_title: str
    points: list[dict[str, Any]]
    draft: dict[str, Any] | None
    guardrail: dict[str, Any] | None
    rule_labels: dict[str, str]
    notes: list[dict[str, Any]]
    excluded_notes: list[dict[str, Any]]
    fallback_message: str | None
    warnings: list[str]
    events: list[dict[str, Any]]
    model_name: str | None
    has_email: bool


class InterviewFeedback(BaseModel):
    interview_id: int
    candidate_id: int
    job_id: int
    assessment_ready: bool
    feedback: Detail | None


@router.post(
    "/candidates/{candidate_id}/feedback",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Draft feedback to a candidate in the background, then wait for a person's review",
)
def generate(
    candidate_id: int,
    body: GenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> Started:
    row, job_id = service.start(
        db,
        candidate_id,
        body.job_id,
        body.stage,
        body.outcome,
        body.interview_id,
        [n.model_dump() for n in body.notes] if body.notes is not None else None,
        user,
    )
    return Started(feedback_id=row.id, background_job_id=job_id)


@router.get(
    "/feedback/{feedback_id}",
    response_model=Detail,
    summary="A feedback letter with its points, evidence and guardrail results (no AI call)",
)
def get_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Detail:
    return Detail(**service.detail(db, feedback_id))


@router.post(
    "/feedback/{feedback_id}/review",
    response_model=ReviewResult,
    summary="Approve, edit (checked again), regenerate or discard a draft",
)
def review(
    feedback_id: int,
    body: ReviewRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> ReviewResult:
    from app.worker.tasks import _feedback_graph

    row, job_id = service.review(
        db,
        feedback_id,
        body.action,
        user,
        _feedback_graph,
        subject=body.subject,
        body=body.body,
        instruction=body.instruction,
    )
    if job_id:
        response.status_code = status.HTTP_202_ACCEPTED
    return ReviewResult(status=row.status, background_job_id=job_id)


@router.post(
    "/feedback/{feedback_id}/send",
    response_model=Detail,
    summary="Send approved feedback once, by email or to the local outbox",
)
def send(
    feedback_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> Detail:
    service.send(db, feedback_id, user)
    return Detail(**service.detail(db, feedback_id))


@router.get(
    "/interviews/{interview_id}/feedback",
    response_model=InterviewFeedback,
    summary="The interview-stage feedback for this interview's candidate, if any",
)
def interview_feedback(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> InterviewFeedback:
    return InterviewFeedback(**service.for_interview(db, interview_id))


@router.get(
    "/jobs/{job_id}/feedback",
    response_model=list[Summary],
    summary="Feedback for a job's candidates: the review queue",
)
def job_queue(
    job_id: int,
    stage: Literal["screening", "interview"] | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[Summary]:
    return [Summary(**s) for s in service.job_queue(db, job_id, stage)]


@router.post(
    "/jobs/{job_id}/feedback/not-selected",
    response_model=Marked,
    summary="Mark candidates as not selected for interview; screening feedback is only for them",
)
def mark(
    job_id: int,
    body: MarkRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> Marked:
    return Marked(marked=service.mark_not_selected(db, job_id, body.candidate_ids, user))


@router.delete(
    "/jobs/{job_id}/feedback/not-selected/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take back a not-selected mark and its unsent feedback",
)
def unmark(
    job_id: int,
    candidate_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> Response:
    service.unmark(db, job_id, candidate_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/jobs/{job_id}/feedback/screening-batch",
    response_model=BatchStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Draft screening feedback for every candidate marked not selected",
)
def batch(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> BatchStarted:
    job_id_, count = service.start_batch(db, job_id, user)
    return BatchStarted(background_job_id=job_id_, candidate_count=count)
