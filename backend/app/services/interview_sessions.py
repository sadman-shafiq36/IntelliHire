# the live interview: the interviewer opens the room, the candidate joins through their
# link, the interviewer ends it. who was in the room and when comes from livekit's
# webhooks, so a closed tab or a dropped connection is recorded too

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.candidate import Candidate
from app.models.enums import InterviewStatus, SpeakerRole
from app.models.interview import Interview, InterviewParticipant, TranscriptTurn
from app.models.job import Job
from app.models.user import User
from app.realtime import status as transcription
from app.services.interviews import _aware, _pending, by_link
from app.tools import livekit

log = get_logger(__name__)

SPEAKER_LABELS = {SpeakerRole.INTERVIEWER: "Interviewer", SpeakerRole.CANDIDATE: "Candidate"}


def interviewer_identity(user_id: int) -> str:
    return f"interviewer-{user_id}"


def candidate_identity(candidate_id: int) -> str:
    return f"candidate-{candidate_id}"


def role_of(identity: str) -> SpeakerRole | None:
    prefix = identity.split("-", 1)[0]
    return {"interviewer": SpeakerRole.INTERVIEWER, "candidate": SpeakerRole.CANDIDATE}.get(prefix)


def _get(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    return interview


def _connection(identity: str, interview: Interview) -> dict[str, Any]:
    room = livekit.room_name(interview.id)
    role = role_of(identity)
    # the role, not the person's name: the screens know the names, livekit doesn't need them
    return {
        "url": settings.livekit_public_url,
        "token": livekit.participant_token(identity, SPEAKER_LABELS[role], room, role.value),
        "room": room,
        "identity": identity,
    }


# --- lifecycle -------------------------------------------------------------------------


def start(db: Session, interview_id: int, user: User) -> dict[str, Any]:
    # opening the room starts the session; opening it again rejoins the same one
    interview = _get(db, interview_id)
    if interview.status == InterviewStatus.COMPLETED:
        raise ConflictError("This interview has ended. Its transcript is in the Interviews step.")
    if interview.status not in (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS):
        raise InvalidInputError("Only a scheduled interview can be started.")
    if _pending(interview):
        raise ConflictError("This interview is being rescheduled or cancelled. Wait a moment.")
    connection = _connection(interviewer_identity(user.id), interview)
    if interview.status == InterviewStatus.SCHEDULED:
        interview.status = InterviewStatus.IN_PROGRESS
        interview.started_at = datetime.now(UTC)
        db.commit()
        log.info("interview_session_started", interview_id=interview.id)
    return connection


def candidate_join(db: Session, link_token: str) -> dict[str, Any]:
    interview = by_link(db, link_token)
    if interview.status == InterviewStatus.CANCELLED:
        raise ConflictError("This interview was cancelled.")
    if interview.status == InterviewStatus.COMPLETED:
        raise ConflictError("This interview has ended. Thank you for your time.")
    if interview.status != InterviewStatus.IN_PROGRESS:
        raise ConflictError(
            "The interviewer hasn't opened the room yet. This page lets you in as soon as they do."
        )
    return _connection(candidate_identity(interview.candidate_id), interview)


def end(db: Session, interview_id: int, reason: str = "ended") -> Interview:
    interview = _get(db, interview_id)
    if interview.status == InterviewStatus.COMPLETED:
        return interview
    if interview.status != InterviewStatus.IN_PROGRESS:
        raise InvalidInputError("Only an interview in progress can be ended.")
    now = datetime.now(UTC)
    interview.status = InterviewStatus.COMPLETED
    interview.ended_at = now
    _close_open_rows(db, interview.id, now)
    db.commit()
    log.info("interview_session_ended", interview_id=interview.id, reason=reason)
    # the transcriber sees the status, finishes what it heard and leaves on its own.
    # closing the room only sends the others out, so a failure here loses nothing
    try:
        livekit.close_room(livekit.room_name(interview.id))
    except Exception:
        log.warning("interview_room_not_closed", interview_id=interview.id)
    return interview


def _close_open_rows(db: Session, interview_id: int, when: datetime, identity: str | None = None):
    query = select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview_id, InterviewParticipant.left_at.is_(None)
    )
    if identity:
        query = query.where(InterviewParticipant.identity == identity)
    for row in db.scalars(query):
        row.left_at = when


# --- livekit webhooks ------------------------------------------------------------------


def on_room_event(db: Session, event: Any) -> None:
    interview_id = livekit.interview_id_from_room(event.room.name) if event.room else None
    if interview_id is None or db.get(Interview, interview_id) is None:
        return
    when = datetime.fromtimestamp(event.created_at, UTC) if event.created_at else datetime.now(UTC)
    if event.event == "room_finished":
        _close_open_rows(db, interview_id, when)
        db.commit()
        return
    identity = event.participant.identity if event.participant else ""
    role = role_of(identity)
    if role is None:
        # the transcriber, or anyone else who isn't an interview participant
        return
    if event.event == "participant_joined":
        # a reconnect after a dropped connection gets its own row
        _close_open_rows(db, interview_id, when, identity)
        user_id = int(identity.split("-", 1)[1]) if role == SpeakerRole.INTERVIEWER else None
        db.add(
            InterviewParticipant(
                interview_id=interview_id,
                role=role,
                user_id=user_id,
                identity=identity,
                display_label=SPEAKER_LABELS[role],
                joined_at=when,
            )
        )
        log.info("participant_joined", interview_id=interview_id, role=role.value)
    elif event.event == "participant_left":
        _close_open_rows(db, interview_id, when, identity)
        log.info("participant_left", interview_id=interview_id, role=role.value)
    db.commit()


# --- reading ---------------------------------------------------------------------------


def _turn(t: TranscriptTurn) -> dict[str, Any]:
    return {
        "id": t.id,
        "sequence": t.sequence,
        "speaker_role": t.speaker_role.value,
        "speaker_label": t.speaker_label,
        "text": t.text,
        "start_offset_ms": t.start_offset_ms,
        "end_offset_ms": t.end_offset_ms,
        "spoken_at": _aware(t.spoken_at),
        "is_uncertain": t.is_uncertain,
        "stt_provider": t.stt_provider,
    }


def turn_payload(t: TranscriptTurn) -> dict[str, Any]:
    # the same shape the transcript api returns, for lines pushed into the room
    out = _turn(t)
    out["spoken_at"] = out["spoken_at"].isoformat() if out["spoken_at"] else None
    return out


def save_turn(
    db: Session,
    interview_id: int,
    role: SpeakerRole,
    text: str,
    started_at: datetime,
    ended_at: datetime,
    uncertain: bool,
    provider: str,
) -> dict[str, Any]:
    # one writer per interview (the transcriber in that room), so max + 1 is safe
    interview = _get(db, interview_id)
    base = _aware(interview.started_at) or started_at
    last = db.scalar(
        select(func.max(TranscriptTurn.sequence)).where(TranscriptTurn.interview_id == interview_id)
    )
    turn = TranscriptTurn(
        interview_id=interview_id,
        sequence=(last or 0) + 1,
        speaker_role=role,
        speaker_label=SPEAKER_LABELS[role],
        text=text,
        start_offset_ms=max(0, int((started_at - base).total_seconds() * 1000)),
        end_offset_ms=max(0, int((ended_at - base).total_seconds() * 1000)),
        spoken_at=started_at,
        is_uncertain=uncertain,
        stt_provider=provider,
    )
    db.add(turn)
    db.commit()
    return turn_payload(turn)


def live_interviews(db: Session) -> list[int]:
    return list(
        db.scalars(select(Interview.id).where(Interview.status == InterviewStatus.IN_PROGRESS))
    )


def details(db: Session, interview_id: int) -> dict[str, Any]:
    interview = _get(db, interview_id)
    job = db.get(Job, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    rows = db.scalars(
        select(InterviewParticipant)
        .where(InterviewParticipant.interview_id == interview.id)
        .order_by(InterviewParticipant.joined_at)
    ).all()
    turns = db.scalars(
        select(TranscriptTurn.id).where(TranscriptTurn.interview_id == interview.id)
    ).all()
    return {
        "id": interview.id,
        "job_id": interview.job_id,
        "job_title": job.title if job else "",
        "candidate_id": interview.candidate_id,
        "candidate_name": candidate.full_name if candidate else "",
        "status": interview.status.value,
        "scheduled_start": interview.scheduled_start,
        "scheduled_end": interview.scheduled_end,
        "timezone_name": interview.timezone_name,
        "duration_minutes": interview.duration_minutes,
        "started_at": _aware(interview.started_at),
        "ended_at": _aware(interview.ended_at),
        "attendance": [
            {
                "role": r.role.value,
                "joined_at": _aware(r.joined_at),
                "left_at": _aware(r.left_at),
            }
            for r in rows
        ],
        "turn_count": len(turns),
        "transcription": (
            transcription.read(interview.id)
            if interview.status == InterviewStatus.IN_PROGRESS
            else {"state": "finished" if interview.ended_at else "stopped", "message": ""}
        ),
    }


def transcript(db: Session, interview_id: int, after: int = 0) -> dict[str, Any]:
    interview = _get(db, interview_id)
    turns = db.scalars(
        select(TranscriptTurn)
        .where(TranscriptTurn.interview_id == interview.id, TranscriptTurn.sequence > after)
        .order_by(TranscriptTurn.start_offset_ms, TranscriptTurn.sequence)
    ).all()
    return {
        "interview_id": interview.id,
        "status": interview.status.value,
        "started_at": _aware(interview.started_at),
        "ended_at": _aware(interview.ended_at),
        "turns": [_turn(t) for t in turns],
    }
