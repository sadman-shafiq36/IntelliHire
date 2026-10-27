# scheduling interviews for candidates a person selected. the api records the request
# and queues it; the worker runs the scheduling agent (calendar and email through the
# mcp server) and saves the result here

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runner import new_thread_id, run_graph
from app.agents.scheduling.graph import Contact
from app.agents.scheduling.slots import parse
from app.config import settings
from app.errors import AppError, ConflictError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.candidate import Candidate
from app.models.enums import IntegrationProvider, InterviewStatus
from app.models.integration import IntegrationAccount
from app.models.interview import Interview
from app.models.job import Job
from app.models.screening import CandidateScore
from app.models.user import User
from app.services import integrations
from app.services.jobs import get_job

log = get_logger(__name__)

ProgressFn = Callable[..., None]
DURATIONS = (30, 45, 60, 90)
STALE_PENDING = timedelta(minutes=10)
ACTIVE = (InterviewStatus.REQUESTED, InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS)

STEP_MESSAGES = {
    "plan": "Reading the request.",
    "interpret": "Working out the times you described.",
    "find_slot": "Checking the calendar.",
    "book": "Booking the calendar event.",
    "draft": "Writing the email.",
    "check": "Checking the email.",
    "send": "Sending the email.",
    "release": "Removing the calendar event.",
}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def channel(db: Session) -> dict[str, Any]:
    account = integrations.scheduling_account(db)
    if account is not None:
        return {"channel": "google", "account": account.account_id}
    google = db.scalar(
        select(IntegrationAccount).where(IntegrationAccount.provider == IntegrationProvider.GOOGLE)
    )
    return {
        "channel": "smtp",
        "account": None,
        "needs_reconnect": google is not None,
    }


# --- reading ------------------------------------------------------------------------


def _row(i: Interview, name: str) -> dict[str, Any]:
    return {
        "id": i.id,
        "candidate_id": i.candidate_id,
        "full_name": name,
        "status": i.status.value,
        "scheduled_start": i.scheduled_start,
        "scheduled_end": i.scheduled_end,
        "timezone_name": i.timezone_name,
        "duration_minutes": i.duration_minutes,
        "has_calendar_event": bool(i.calendar_event_id),
        "invitation_channel": i.invitation_channel,
        "invitation_sent_at": i.invitation_sent_at,
        "pending_action": i.pending_action if _pending(i) else None,
        "last_outcome": i.last_outcome,
        "last_error": i.last_error,
        "cancellation_reason": i.cancellation_reason,
        "started_at": _aware(i.started_at),
        "ended_at": _aware(i.ended_at),
    }


def _pending(i: Interview) -> bool:
    since = _aware(i.pending_since)
    return bool(i.pending_action and since and datetime.now(UTC) - since < STALE_PENDING)


def for_job(db: Session, job_id: int) -> dict[str, Any]:
    get_job(db, job_id)
    interviews = db.scalars(
        select(Interview).where(Interview.job_id == job_id).order_by(Interview.id.desc())
    ).all()
    selected = db.scalars(
        select(CandidateScore)
        .where(CandidateScore.job_id == job_id, CandidateScore.selected_at.is_not(None))
        .order_by(CandidateScore.rank)
    ).all()
    ids = {i.candidate_id for i in interviews} | {s.candidate_id for s in selected}
    people = {c.id: c for c in db.scalars(select(Candidate).where(Candidate.id.in_(ids)))}
    busy = {i.candidate_id for i in interviews if i.status in ACTIVE}
    # the latest attempt per candidate; older cancelled or failed ones stay listed below it
    return {
        **channel(db),
        "to_schedule": [
            {
                "candidate_id": s.candidate_id,
                "full_name": people[s.candidate_id].full_name,
                "has_email": bool(people[s.candidate_id].email),
                "rank": s.rank,
                "aggregate_score": s.aggregate_score,
            }
            for s in selected
            if s.candidate_id not in busy and s.candidate_id in people
        ],
        "interviews": [_row(i, people[i.candidate_id].full_name) for i in interviews],
    }


# --- requests from the api ------------------------------------------------------------


def _check_options(options: dict[str, Any]) -> None:
    try:
        ZoneInfo(options.get("time_zone") or "")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidInputError("That time zone is not recognised.") from exc
    if options.get("duration_minutes") not in DURATIONS:
        raise InvalidInputError("Choose a length of 30, 45, 60 or 90 minutes.")
    if not options.get("start") and not (options.get("preference") or "").strip():
        raise InvalidInputError("Choose a time, or describe when the interview should be.")
    if options.get("start"):
        try:
            parse(options["start"], ZoneInfo(options["time_zone"]))
        except ValueError as exc:
            raise InvalidInputError("That date and time could not be read.") from exc


def _claim(i: Interview, action: str) -> None:
    if _pending(i):
        raise ConflictError("This interview is already being updated. Wait for it to finish.")
    i.pending_action = action
    i.pending_since = datetime.now(UTC)
    i.last_outcome = None
    i.last_error = None


def request_schedule(
    db: Session, job_id: int, candidate_id: int, options: dict[str, Any], user: User
) -> Interview:
    get_job(db, job_id)
    _check_options(options)
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job_id, CandidateScore.candidate_id == candidate_id
        )
    )
    if score is None or score.selected_at is None:
        raise InvalidInputError("Select this candidate for interview in the Scoring step first.")
    candidate = db.get(Candidate, candidate_id)
    if not candidate or not candidate.email:
        raise InvalidInputError(
            "There is no email address for this candidate, so an invitation can't be sent."
        )
    existing = db.scalars(
        select(Interview).where(Interview.job_id == job_id, Interview.candidate_id == candidate_id)
    ).all()
    if any(i.status in ACTIVE for i in existing):
        raise ConflictError("This candidate already has an interview. Reschedule it instead.")
    # the link isn't sent until the agent has booked the time, so this token is a stand-in
    _, placeholder = _new_token()
    interview = Interview(
        job_id=job_id,
        candidate_id=candidate_id,
        interviewer_id=user.id,
        status=InterviewStatus.REQUESTED,
        timezone_name=options["time_zone"],
        duration_minutes=options["duration_minutes"],
        join_token=placeholder,
    )
    _claim(interview, "schedule")
    db.add(interview)
    db.commit()
    log.info("interview_requested", interview_id=interview.id, job_id=job_id)
    return interview


def _get(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    return interview


def request_change(
    db: Session, interview_id: int, action: str, options: dict[str, Any]
) -> tuple[Interview, str]:
    # returns the action the worker should run: a failed booking tried again is a fresh
    # invitation, not a "your interview has moved" email
    interview = _get(db, interview_id)
    if action == "reschedule":
        _check_options(options)
        if interview.status == InterviewStatus.FAILED:
            action = "schedule"
            interview.status = InterviewStatus.REQUESTED
            interview.timezone_name = options["time_zone"]
            interview.duration_minutes = options["duration_minutes"]
        elif interview.status != InterviewStatus.SCHEDULED:
            raise InvalidInputError("Only a scheduled interview can be moved.")
    elif interview.status == InterviewStatus.FAILED:
        # nothing was booked or sent, so there is nobody to tell
        interview.status = InterviewStatus.CANCELLED
        interview.cancellation_reason = (options.get("reason") or "")[:500] or None
        interview.last_outcome = "cancelled"
        db.commit()
        return interview, "none"
    elif interview.status != InterviewStatus.SCHEDULED:
        raise InvalidInputError("Only a scheduled interview can be cancelled.")
    _claim(interview, action)
    db.commit()
    return interview, action


# --- the worker's side ------------------------------------------------------------------


def run_action(
    db: Session,
    interview_id: int,
    action: str,
    options: dict[str, Any],
    make_graph: Callable[[Any, Contact], Any],
    make_tools: Callable[[str | None, str | None], Any],
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    interview = _get(db, interview_id)
    job: Job = get_job(db, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    interviewer = db.get(User, interview.interviewer_id) if interview.interviewer_id else None

    def report(message: str) -> None:
        if progress:
            progress(state="running", message=message, total=0, completed=0)

    account = integrations.scheduling_account(db)
    token = integrations.access_token(db, IntegrationProvider.GOOGLE)[0] if account else None
    tools = make_tools(token, integrations.mail_from(account) if token else None)

    # a reschedule or retry issues a new link; the old one stops working
    link_token = hash_value = None
    if action in ("schedule", "reschedule"):
        link_token, hash_value = _new_token()
    join_url = f"{settings.public_base_url}/interview/{link_token}" if link_token else None
    contact = Contact(candidate.full_name, candidate.email or "", join_url)

    tz_name = options.get("time_zone") or interview.timezone_name
    duration = options.get("duration_minutes") or interview.duration_minutes
    existing = None
    if interview.status == InterviewStatus.SCHEDULED and interview.scheduled_start:
        existing = {
            "event_id": interview.calendar_event_id,
            "start": _aware(interview.scheduled_start).isoformat(),
            "end": _aware(interview.scheduled_end).isoformat(),
            "ics_uid": interview.ics_uid,
            "ics_sequence": interview.ics_sequence,
        }
    state = {
        "thread_id": new_thread_id("schedule"),
        "action": action,
        "job_title": job.title,
        "interviewer_name": (interviewer.full_name if interviewer else None) or "The hiring team",
        "organizer_email": account.account_id if account else settings.smtp_from_email,
        "interviewer_url": f"{settings.public_base_url}/jobs/{job.id}?tab=interviews",
        "time_zone": tz_name,
        "duration_minutes": duration,
        "now": datetime.now(ZoneInfo(tz_name)).isoformat(),
        "start": options.get("start"),
        "preference": options.get("preference"),
        "allow_conflict": bool(options.get("allow_conflict")),
        "note": options.get("note"),
        "reason": options.get("reason"),
        "existing": existing,
        "channel": tools.channel,
        "errors": [],
        "warnings": [],
    }
    graph = make_graph(tools, contact)

    def invoke() -> dict[str, Any]:
        from app.agents.checkpointer import thread_config

        final: dict[str, Any] = {}
        for mode, chunk in graph.stream(
            state, config=thread_config(state["thread_id"]), stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = chunk
            else:
                for node in chunk:
                    nxt = _next_message(node, order)
                    if nxt:
                        report(nxt)
        return final

    order = (
        ["plan", "draft", "check", "send", "release"]
        if action == "cancel"
        else ["plan", *([] if options.get("start") else ["interpret"]), "find_slot", "book",
              "draft", "check", "send"]
    )  # fmt: skip
    report(STEP_MESSAGES["plan"])
    final = run_graph(
        db,
        "interview_scheduling",
        state["thread_id"],
        invoke,
        # ids only; deleting the candidate finds this run, and its checkpoints, by them
        {
            "job_id": job.id,
            "candidate_id": candidate.id,
            "interview_id": interview.id,
            "action": action,
        },
        failure_code=lambda f: (
            None
            if f.get("outcome") in ("scheduled", "rescheduled", "cancelled")
            else (f.get("outcome") or "failed")
        ),
    )
    return _save(db, interview, action, final, hash_value, tz_name, duration)


def _next_message(node: str, order: list[str]) -> str | None:
    # updates arrive when a step finishes, so announce the one after it
    if node not in order or node == order[-1]:
        return None
    return STEP_MESSAGES.get(order[order.index(node) + 1])


def _save(
    db: Session,
    interview: Interview,
    action: str,
    final: dict[str, Any],
    hash_value: str | None,
    tz_name: str,
    duration: int,
) -> dict[str, Any]:
    outcome = final.get("outcome") or "failed"
    interview.pending_action = None
    interview.pending_since = None
    interview.last_outcome = outcome
    warnings = final.get("warnings") or []
    if outcome in ("scheduled", "rescheduled"):
        tz = ZoneInfo(tz_name)
        start = parse(final["slot_start"], tz)
        end = parse(final["slot_end"], tz)
        interview.status = InterviewStatus.SCHEDULED
        interview.scheduled_start = start
        interview.scheduled_end = end
        interview.timezone_name = tz_name
        interview.duration_minutes = duration
        interview.calendar_event_id = final.get("event_id") or interview.calendar_event_id
        interview.join_token = hash_value
        interview.join_token_expires_at = end + timedelta(days=settings.interview_link_valid_days)
        interview.ics_uid = final.get("ics_uid")
        interview.ics_sequence = final.get("ics_sequence", 0)
        interview.invitation_channel = "gmail" if final.get("channel") == "google" else "smtp"
        interview.invitation_sent_at = datetime.now(UTC)
        interview.last_error = "; ".join(warnings)[:500] or None
    elif outcome == "cancelled":
        interview.status = InterviewStatus.CANCELLED
        interview.cancellation_reason = (final.get("reason") or "")[:500] or None
        interview.ics_sequence = final.get("ics_sequence", interview.ics_sequence)
        interview.invitation_sent_at = datetime.now(UTC)
        interview.last_error = "; ".join(warnings)[:500] or None
    else:
        # nothing changed for the candidate: a new booking is marked failed, an existing
        # one stays as it was with the reason beside it
        if action == "schedule":
            interview.status = InterviewStatus.FAILED
        interview.last_error = (final.get("message") or "The interview could not be booked.")[:500]
    db.commit()
    log.info("interview_action_done", interview_id=interview.id, action=action, outcome=outcome)
    if outcome in ("scheduled", "rescheduled"):
        _after_booking(db, interview)
    result = {
        "interview_id": interview.id,
        "outcome": outcome,
        "message": final.get("message"),
        "warnings": warnings,
        "busy": final.get("busy") or [],
    }
    if outcome not in ("scheduled", "rescheduled", "cancelled"):
        raise AppError(interview.last_error or "The interview could not be booked.", result)
    return result


def _after_booking(db: Session, interview: Interview) -> None:
    # a face template is kept until a few days after the interview, and a form photo
    # becomes one now. never fails the booking: without it the interviewer confirms by hand
    from app.services import identity
    from app.worker import queue

    try:
        identity.keep_until(db, interview.candidate_id, interview.scheduled_end)
        db.commit()
        if identity.needs_preparing(db, interview.candidate_id):
            queue.enqueue(
                "app.worker.tasks.prepare_face_reference_task",
                "face_reference",
                candidate_id=interview.candidate_id,
                interview_id=interview.id,
            )
    except Exception:
        db.rollback()
        log.warning("face_reference_not_queued", interview_id=interview.id)


# --- the candidate's link ---------------------------------------------------------------


def by_link(db: Session, token: str) -> Interview:
    interview = db.scalar(select(Interview).where(Interview.join_token == hash_token(token)))
    if interview is None or interview.status in (
        InterviewStatus.REQUESTED,
        InterviewStatus.FAILED,
    ):
        raise NotFoundError(
            "This interview link is not valid. Use the link in the most recent email you received."
        )
    expires = _aware(interview.join_token_expires_at)
    if expires and expires < datetime.now(UTC):
        raise NotFoundError("This interview link has expired.")
    return interview


def join_details(db: Session, token: str) -> dict[str, Any]:
    interview = by_link(db, token)
    job = db.get(Job, interview.job_id)
    interviewer = db.get(User, interview.interviewer_id) if interview.interviewer_id else None
    # nothing about scores or other candidates, and not even the candidate's own details.
    # the call itself happens on this page, in intellihire, never google meet
    return {
        "job_title": job.title,
        "status": interview.status.value,
        "scheduled_start": interview.scheduled_start,
        "scheduled_end": interview.scheduled_end,
        "timezone_name": interview.timezone_name,
        "interviewer_name": interviewer.full_name if interviewer else None,
    }


def counts_by_job(db: Session) -> dict[int, dict[str, int]]:
    # for the jobs list and the overview: booked, still to come, selected but not booked,
    # held, and going on right now
    out: dict[int, dict[str, int]] = {}
    active: set[tuple[int, int]] = set()

    def blank() -> dict[str, int]:
        return {"scheduled": 0, "upcoming": 0, "to_schedule": 0, "completed": 0, "live": 0}

    for i in db.scalars(select(Interview)):
        if i.status in ACTIVE:
            active.add((i.job_id, i.candidate_id))
        row = out.setdefault(i.job_id, blank())
        if i.status == InterviewStatus.COMPLETED:
            row["completed"] += 1
        if i.status == InterviewStatus.IN_PROGRESS:
            row["live"] += 1
        if i.status in (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS):
            row["scheduled"] += 1
            end = _aware(i.scheduled_end)
            if end and end > datetime.now(UTC):
                row["upcoming"] += 1
    for s in db.scalars(select(CandidateScore).where(CandidateScore.selected_at.is_not(None))):
        if (s.job_id, s.candidate_id) not in active:
            row = out.setdefault(s.job_id, blank())
            row["to_schedule"] += 1
    return out
