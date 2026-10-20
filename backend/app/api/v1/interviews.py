from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import interviews as service
from app.worker import queue

router = APIRouter(tags=["interviews"])


class ScheduleOptions(BaseModel):
    duration_minutes: int = 45
    time_zone: str = Field(description="IANA name, e.g. Asia/Dhaka")
    start: str | None = Field(None, description="exact local start, e.g. 2026-10-20T15:00")
    preference: str | None = Field(
        None, max_length=300, description="or describe it, e.g. 'Tuesday or Wednesday afternoon'"
    )
    allow_conflict: bool = False
    note: str | None = Field(None, max_length=500)


class ScheduleIn(ScheduleOptions):
    candidate_id: int


class CancelIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


class Started(BaseModel):
    interview_id: int
    background_job_id: str | None


class InterviewRow(BaseModel):
    id: int
    candidate_id: int
    full_name: str
    status: str
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    timezone_name: str
    duration_minutes: int
    has_calendar_event: bool
    invitation_channel: str | None
    invitation_sent_at: datetime | None
    pending_action: str | None
    last_outcome: str | None
    last_error: str | None
    cancellation_reason: str | None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ToSchedule(BaseModel):
    candidate_id: int
    full_name: str
    has_email: bool
    rank: int | None
    aggregate_score: float


class JobInterviews(BaseModel):
    channel: str
    account: str | None
    needs_reconnect: bool = False
    to_schedule: list[ToSchedule]
    interviews: list[InterviewRow]


class JoinDetails(BaseModel):
    job_title: str
    status: str
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    timezone_name: str
    interviewer_name: str | None


def _queue(interview_id: int, action: str, options: dict[str, Any]) -> str:
    return queue.enqueue(
        "app.worker.tasks.schedule_interview_task",
        "interview_scheduling",
        interview_id=interview_id,
        action=action,
        options=options,
    )


@router.get(
    "/jobs/{job_id}/interviews",
    response_model=JobInterviews,
    summary="Candidates selected for interview, the job's interviews, and how invites are sent",
)
def list_interviews(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobInterviews:
    return JobInterviews(**service.for_job(db, job_id))


@router.post(
    "/jobs/{job_id}/interviews",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Schedule an interview: the agent books it and emails the candidate (background job)",
)
def schedule(
    job_id: int,
    payload: ScheduleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> Started:
    options = payload.model_dump(exclude={"candidate_id"})
    interview = service.request_schedule(db, job_id, payload.candidate_id, options, user)
    return Started(
        interview_id=interview.id, background_job_id=_queue(interview.id, "schedule", options)
    )


@router.post(
    "/interviews/{interview_id}/reschedule",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Move an interview, or try a failed booking again (background job)",
)
def reschedule(
    interview_id: int,
    payload: ScheduleOptions,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Started:
    options = payload.model_dump()
    interview, action = service.request_change(db, interview_id, "reschedule", options)
    return Started(
        interview_id=interview.id, background_job_id=_queue(interview.id, action, options)
    )


@router.post(
    "/interviews/{interview_id}/cancel",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel an interview: the candidate is told, then the event is removed",
)
def cancel(
    interview_id: int,
    payload: CancelIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Started:
    options = payload.model_dump()
    interview, action = service.request_change(db, interview_id, "cancel", options)
    job_id = None if action == "none" else _queue(interview.id, action, options)
    return Started(interview_id=interview.id, background_job_id=job_id)


@router.get(
    "/interviews/join/{token}",
    response_model=JoinDetails,
    summary="The candidate's secure link: their interview's time and video link (no sign-in)",
)
def join(token: str, db: Session = Depends(get_db)) -> JoinDetails:
    return JoinDetails(**service.join_details(db, token))
