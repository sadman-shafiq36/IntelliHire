from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.errors import AuthenticationError
from app.logging_config import get_logger
from app.models.user import User
from app.services import interview_sessions as service
from app.tools import livekit

log = get_logger(__name__)

router = APIRouter(tags=["interview room"])


class Connection(BaseModel):
    url: str
    token: str
    room: str
    identity: str


class Attendance(BaseModel):
    role: str
    joined_at: datetime | None
    left_at: datetime | None


class TranscriptionState(BaseModel):
    state: str
    message: str = ""
    provider: str | None = None
    backlog: int | None = None


class SessionDetails(BaseModel):
    id: int
    job_id: int
    job_title: str
    candidate_id: int
    candidate_name: str
    status: str
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    timezone_name: str
    duration_minutes: int
    started_at: datetime | None
    ended_at: datetime | None
    attendance: list[Attendance]
    turn_count: int
    transcription: TranscriptionState


class Turn(BaseModel):
    id: int
    sequence: int
    speaker_role: str
    speaker_label: str
    text: str
    start_offset_ms: int
    end_offset_ms: int
    spoken_at: datetime | None
    is_uncertain: bool
    stt_provider: str | None


class Transcript(BaseModel):
    interview_id: int
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    turns: list[Turn]


@router.post(
    "/interviews/{interview_id}/session",
    response_model=Connection,
    summary="Open the interview room (starts the session, or rejoins it)",
)
def open_room(
    interview_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> Connection:
    return Connection(**service.start(db, interview_id, user))


@router.get(
    "/interviews/{interview_id}/session",
    response_model=SessionDetails,
    summary="The session record: times, who joined and left, and how transcription is going",
)
def session_details(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> SessionDetails:
    return SessionDetails(**service.details(db, interview_id))


@router.post(
    "/interviews/{interview_id}/session/end",
    response_model=SessionDetails,
    summary="End the interview for everyone; the transcript is kept",
)
def end_session(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> SessionDetails:
    service.end(db, interview_id)
    return SessionDetails(**service.details(db, interview_id))


@router.get(
    "/interviews/{interview_id}/transcript",
    response_model=Transcript,
    summary="Speaker-attributed, timestamped transcript; `after` returns only newer turns",
)
def get_transcript(
    interview_id: int,
    after: int = Query(0, ge=0, description="last sequence number already received"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Transcript:
    return Transcript(**service.transcript(db, interview_id, after))


@router.post(
    "/interviews/join/{token}/session",
    response_model=Connection,
    summary="The candidate joins the room through their secure link (no sign-in)",
)
def candidate_join(token: str, db: Session = Depends(get_db)) -> Connection:
    return Connection(**service.candidate_join(db, token))


@router.post(
    "/livekit/webhook",
    include_in_schema=False,
    summary="Room events from the LiveKit server: who joined and left",
)
async def livekit_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    body = (await request.body()).decode()
    try:
        event = livekit.read_webhook(body, request.headers.get("Authorization", ""))
    except Exception as exc:
        log.warning("livekit_webhook_rejected", error_type=type(exc).__name__)
        raise AuthenticationError("Not a valid event.") from exc
    # the database calls are blocking, keep them off the event loop
    await run_in_threadpool(service.on_room_event, db, event)
    return {"ok": True}
