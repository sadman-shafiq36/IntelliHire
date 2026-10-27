# the candidate feedback composer: who may get feedback and when, reading the stored evidence
# for the graph, running it and resuming it from a person's review, and sending the approved
# letter. the candidate's name and address are read only to send it, never put in the graph.
# screening feedback is only ever for someone a person marked "not selected for interview";
# it is never worked out from the ranking

import html
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.checkpointer import thread_config
from app.agents.feedback.checks import OUTCOMES, RULES
from app.agents.feedback.llm import PROMPT_VERSION
from app.agents.runner import new_thread_id, run_graph
from app.config import settings
from app.errors import (
    AppError,
    ConflictError,
    ExternalServiceError,
    InvalidInputError,
    NotFoundError,
)
from app.logging_config import get_logger
from app.models.analysis import Assessment, AssessmentFinding, QaPair
from app.models.candidate import Candidate, CvExtraction
from app.models.enums import IntegrationProvider, InterviewStatus, ScreeningStatus
from app.models.feedback import CandidateFeedback
from app.models.interview import Interview
from app.models.screening import (
    CandidateScore,
    ScoreDimension,
    Screening,
    ScreeningRequirementResult,
)
from app.models.user import User
from app.services import integrations, interview_analysis
from app.services.jobs import get_job
from app.worker import queue

log = get_logger(__name__)

ProgressFn = Callable[..., None]
STALE_RUN = timedelta(minutes=15)
MAX_NOTES = 10
MAX_NOTE_CHARS = 1000
MAX_SUBJECT = 200
MAX_BODY = 6000
NOTE_AUTHORS = ("interviewer", "recruiter")
FALLBACK_LABEL = {
    "low_confidence": "The CV screening wasn't confident enough to give specific feedback.",
    "unreadable": "The CV couldn't be read well enough to give specific feedback.",
    "no_evidence": "There was no stored evidence to base specific feedback on.",
    "ai_unavailable": "The AI service was unavailable, so the generic letter was used.",
    "guardrail": "Every AI draft broke a rule, so the generic letter was used.",
}


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def _get(db: Session, feedback_id: int) -> CandidateFeedback:
    row = db.get(CandidateFeedback, feedback_id)
    if row is None:
        raise NotFoundError("That feedback does not exist.")
    return row


def _event(row: CandidateFeedback, action: str, user_id: int | None = None, **detail: Any) -> None:
    # the audit trail: what happened, by which account, when; ids and codes only
    entry = {"at": datetime.now(UTC).isoformat(), "action": action, "user_id": user_id, **detail}
    row.events = [*(row.events or []), entry]
    log.info("candidate_feedback_event", feedback_id=row.id, action=action, user_id=user_id)


def _busy(row: CandidateFeedback) -> bool:
    return row.status == "drafting" and _aware(row.updated_at) > datetime.now(UTC) - STALE_RUN


def _selected(db: Session, job_id: int, candidate_id: int) -> bool:
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job_id, CandidateScore.candidate_id == candidate_id
        )
    )
    return bool(score and score.selected_at)


def _clean_notes(notes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for n in (notes or [])[:MAX_NOTES]:
        text = " ".join((n.get("text") or "").split())[:MAX_NOTE_CHARS]
        author = n.get("author") if n.get("author") in NOTE_AUTHORS else "recruiter"
        if text:
            out.append({"id": f"N{len(out) + 1}", "author": author, "text": text})
    return out


def _reset(row: CandidateFeedback) -> None:
    row.thread_id = new_thread_id("feedback")
    row.points = row.draft = row.guardrail = row.pending_edit = None
    row.excluded_notes = row.warnings = None
    row.fallback_reason = row.model_name = row.message = None
    row.approved_at = None
    row.reviewed_by_id = None


# --- marking candidates not selected for interview ------------------------------------


def mark_not_selected(db: Session, job_id: int, candidate_ids: list[int], user: User) -> int:
    get_job(db, job_id)
    marked = 0
    for candidate_id in dict.fromkeys(candidate_ids):
        screening = db.scalar(
            select(Screening).where(
                Screening.job_id == job_id, Screening.candidate_id == candidate_id
            )
        )
        if screening is None or screening.status not in (
            ScreeningStatus.SCREENED,
            ScreeningStatus.EXCLUDED,
        ):
            raise InvalidInputError("Only screened candidates can be marked as not selected.")
        if _selected(db, job_id, candidate_id):
            raise ConflictError(
                "This candidate is selected for interview. Unselect them first if that changed."
            )
        row = _row(db, candidate_id, job_id, "screening")
        if row is None:
            row = CandidateFeedback(
                candidate_id=candidate_id,
                job_id=job_id,
                stage="screening",
                outcome="not_progressing",
                status="new",
                notes=[],
            )
            db.add(row)
            db.flush()
        if row.marked_at is None:
            row.marked_at, row.marked_by_id = datetime.now(UTC), user.id
            _event(row, "marked_not_selected", user.id)
            marked += 1
    db.commit()
    return marked


def unmark(db: Session, job_id: int, candidate_id: int, user: User) -> None:
    row = _row(db, candidate_id, job_id, "screening")
    if row is None:
        raise NotFoundError("This candidate isn't marked as not selected.")
    if row.status in ("approved", "sending", "sent") or _busy(row):
        raise ConflictError("Feedback for this candidate is approved or on its way; it stays.")
    log.info("candidate_feedback_event", feedback_id=row.id, action="unmarked", user_id=user.id)
    db.delete(row)
    db.commit()


def _row(db: Session, candidate_id: int, job_id: int, stage: str) -> CandidateFeedback | None:
    return db.scalar(
        select(CandidateFeedback).where(
            CandidateFeedback.candidate_id == candidate_id,
            CandidateFeedback.job_id == job_id,
            CandidateFeedback.stage == stage,
        )
    )


# --- starting --------------------------------------------------------------------------


def _check_can_draft(row: CandidateFeedback) -> None:
    if row.status in ("sending", "sent"):
        raise ConflictError("This feedback has already been sent.")
    if _busy(row):
        raise ConflictError("This feedback is already being written.")


def _enqueue_run(row: CandidateFeedback, resume: bool = False) -> str:
    return queue.enqueue(
        "app.worker.tasks.compose_feedback_task",
        "candidate_feedback",
        feedback_id=row.id,
        resume=resume,
    )


def start(
    db: Session,
    candidate_id: int,
    job_id: int,
    stage: str,
    outcome: str | None,
    interview_id: int | None,
    notes: list[dict[str, Any]] | None,
    user: User,
) -> tuple[CandidateFeedback, str]:
    if stage not in OUTCOMES:
        raise InvalidInputError("Choose screening or interview feedback.")
    get_job(db, job_id)
    if db.get(Candidate, candidate_id) is None:
        raise NotFoundError("That candidate does not exist.")
    if stage == "screening":
        row = _row(db, candidate_id, job_id, "screening")
        if row is None or row.marked_at is None:
            raise InvalidInputError(
                "Screening feedback is only for candidates marked as not selected for "
                "interview. Mark them first."
            )
        if _selected(db, job_id, candidate_id):
            raise ConflictError("This candidate is now selected for interview.")
        outcome = "not_progressing"
    else:
        if outcome not in OUTCOMES["interview"]:
            raise InvalidInputError("Choose an outcome: not progressing, on hold or next round.")
        interview = db.get(Interview, interview_id) if interview_id else None
        if (
            interview is None
            or interview.candidate_id != candidate_id
            or interview.job_id != job_id
        ):
            raise InvalidInputError("Choose the candidate's interview for this job.")
        if interview.status != InterviewStatus.COMPLETED:
            raise InvalidInputError("Interview feedback is written once the interview has ended.")
        assessment = db.scalar(select(Assessment).where(Assessment.interview_id == interview.id))
        if assessment is None or assessment.status != "assessed":
            raise InvalidInputError(
                "The consolidated assessment has to be written first; feedback is built on it."
            )
        row = _row(db, candidate_id, job_id, "interview")
        if row is None:
            row = CandidateFeedback(
                candidate_id=candidate_id, job_id=job_id, stage="interview", status="new"
            )
            db.add(row)
        row.interview_id = interview.id
    _check_can_draft(row)
    row.outcome = outcome
    if notes is not None:
        row.notes = _clean_notes(notes)
    _reset(row)
    row.status = "drafting"
    db.flush()
    _event(row, "generate_requested", user.id, outcome=outcome)
    db.commit()
    return row, _enqueue_run(row)


def start_batch(db: Session, job_id: int, user: User) -> tuple[str, int]:
    get_job(db, job_id)
    rows = [
        r
        for r in db.scalars(
            select(CandidateFeedback).where(
                CandidateFeedback.job_id == job_id,
                CandidateFeedback.stage == "screening",
                CandidateFeedback.marked_at.is_not(None),
                CandidateFeedback.status.in_(["new", "failed"]),
            )
        )
        if not _selected(db, job_id, r.candidate_id)
    ]
    if not rows:
        raise InvalidInputError(
            "Nobody is waiting for screening feedback. Mark candidates as not selected first."
        )
    for row in rows:
        _reset(row)
        row.status = "drafting"
        _event(row, "generate_requested", user.id, outcome="not_progressing", batch=True)
    db.commit()
    job_id_ = queue.enqueue(
        "app.worker.tasks.compose_feedback_batch_task",
        "candidate_feedback_batch",
        feedback_ids=[r.id for r in rows],
    )
    return job_id_, len(rows)


# --- running ---------------------------------------------------------------------------


def read_sources(db: Session, row: CandidateFeedback) -> dict[str, Any]:
    # ids and short texts only; the cv itself stays in the database, only its length goes in
    job = get_job(db, row.job_id)
    requirements = [
        {"id": r.id, "text": r.text, "is_mandatory": r.is_mandatory, "weight": r.weight,
         "category": r.category.value}
        for r in job.requirements
    ]  # fmt: skip
    screening = db.scalar(
        select(Screening).where(
            Screening.job_id == row.job_id, Screening.candidate_id == row.candidate_id
        )
    )
    results = [
        {"id": r.id, "requirement_id": r.job_requirement_id, "status": r.status.value,
         "quote": r.evidence_quote, "verified": r.evidence_verified, "reasoning": r.reasoning,
         "confidence": r.confidence}
        for r in (screening.requirement_results if screening else [])
    ]  # fmt: skip
    if row.stage == "screening":
        cv_chars = 0
        if screening and screening.cv_document_id:
            extraction = db.scalar(
                select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
            )
            cv_chars = len((extraction.raw_text or "").strip()) if extraction else 0
        score = db.scalar(
            select(CandidateScore).where(
                CandidateScore.job_id == row.job_id, CandidateScore.candidate_id == row.candidate_id
            )
        )
        return {
            "screening_status": screening.status.value if screening else None,
            "cv_chars": cv_chars,
            "requirements": requirements,
            "results": results,
            "dimensions": [
                {"id": d.id, "dimension": d.dimension.value, "justification": d.justification}
                for d in (score.dimensions if score else [])
            ],
        }
    pairs = interview_analysis._pairs(db, row.interview_id)
    assessment = db.scalar(select(Assessment).where(Assessment.interview_id == row.interview_id))
    findings = []
    for f in assessment.findings if assessment else []:
        ids = [e["ref_id"] for e in f.evidence or [] if e.get("type") == "score"]
        findings.append({"id": f.id, "kind": f.kind.value, "text": f.text, "requirement_ids": ids})
    return {
        "requirements": requirements,
        "results": results,
        "pairs": [
            {
                "id": p.id,
                "question": p.question_text,
                "justification": p.justification,
                "links": [
                    {"requirement_id": link.job_requirement_id, "coverage": link.coverage.value}
                    for link in p.requirement_links
                ],
            }
            for p in pairs
        ],  # fmt: skip
        "findings": findings,
    }


def _decision(row: CandidateFeedback) -> dict[str, Any]:
    # an edit or a regenerate request waits on the row, so its text never travels in the queue
    pending = row.pending_edit or {}
    action = pending.get("action")
    if action == "edit":
        return {"action": "edit", "subject": pending["subject"], "body": pending["body"],
                "user_id": pending.get("user_id")}  # fmt: skip
    if action == "regenerate":
        return {"action": "regenerate", "instruction": pending.get("instruction"),
                "user_id": pending.get("user_id")}  # fmt: skip
    raise InvalidInputError("There is nothing waiting to be checked.")


def run(
    db: Session,
    feedback_id: int,
    graph: Any,
    decision: dict[str, Any] | None = None,
    resume: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    row = _get(db, feedback_id)
    if progress:
        progress(state="running", message="Writing the feedback and checking it.", total=0,
                 completed=0)  # fmt: skip
    config = thread_config(row.thread_id)
    if resume and decision is None:
        decision = _decision(row)
    if decision is not None:
        payload: Any = Command(resume=decision)
    else:
        job = get_job(db, row.job_id)
        payload = {
            "thread_id": row.thread_id,
            "stage": row.stage,
            "outcome": row.outcome,
            "job": {"title": job.title},
            "sources": read_sources(db, row),
            "notes": row.notes or [],
            "errors": [],
        }
    screening = db.scalar(
        select(Screening).where(
            Screening.job_id == row.job_id, Screening.candidate_id == row.candidate_id
        )
    )

    def invoke() -> dict[str, Any]:
        graph.invoke(payload, config=config)
        snapshot = graph.get_state(config)
        return {**snapshot.values, "paused": bool(snapshot.next)}

    try:
        final = run_graph(
            db,
            graph_name="candidate_feedback",
            thread_id=row.thread_id,
            invoke=invoke,
            # the evidence and the notes are in the checkpoints, so deletion must find this
            context={
                "feedback_id": row.id,
                "candidate_id": row.candidate_id,
                "job_id": row.job_id,
                "interview_id": row.interview_id,
                "cv_document_id": screening.cv_document_id if screening else None,
                "stage": row.stage,
                "action": decision["action"] if decision else "generate",
                "prompt_version": PROMPT_VERSION,
            },
            failure_code=lambda s: None if s.get("paused") or s.get("status") else "failed",
        )
    except ExternalServiceError as exc:
        row.status, row.message, row.pending_edit = "failed", exc.message[:500], None
        _event(row, "failed")
        db.commit()
        raise

    row.points = final.get("points") or []
    row.draft = final.get("draft")
    row.guardrail = final.get("guardrail")
    row.excluded_notes = final.get("excluded_notes") or []
    row.fallback_reason = final.get("fallback_reason")
    row.warnings = list(dict.fromkeys(final.get("warnings") or []))
    row.model_name = final.get("model_name") or "none"
    row.prompt_version = PROMPT_VERSION
    row.pending_edit = None
    user_id = decision.get("user_id") if decision else None
    if final.get("paused"):
        row.status, row.message = "in_review", None
        _event(
            row,
            decision["action"] + "_checked" if decision else "drafted",
            user_id,
            guardrail_passed=bool(row.guardrail and row.guardrail["passed"]),
            fallback=row.fallback_reason,
        )
    elif final.get("status") in ("approved", "discarded"):
        row.status = final["status"]
        row.reviewed_by_id = user_id
        if row.status == "approved":
            row.approved_at = datetime.now(UTC)
        _event(row, row.status, user_id)
    else:
        row.status, row.message = "failed", "The feedback could not be written."
        _event(row, "failed")
    db.commit()
    return {"feedback_id": row.id, "status": row.status}


def run_batch(
    db: Session, feedback_ids: list[int], graph: Any, progress: ProgressFn | None = None
) -> dict[str, Any]:
    # one after another: the free tier can't take them in parallel, and one failure skips one
    done = failed = 0
    for i, feedback_id in enumerate(feedback_ids):
        if progress:
            progress(state="running", message="Writing screening feedback.",
                     total=len(feedback_ids), completed=i)  # fmt: skip
        try:
            run(db, feedback_id, graph)
            done += 1
        except AppError:
            db.rollback()
            failed += 1
    return {"drafted": done, "failed": failed}


# --- reviewing -------------------------------------------------------------------------


def review(
    db: Session, feedback_id: int, action: str, user: User, graph_factory: Any, **fields: Any
) -> tuple[CandidateFeedback, str | None]:
    # approve and discard answer at once; an edit or a regenerate needs the ai, so it is queued
    row = _get(db, feedback_id)
    if row.status != "in_review":
        raise ConflictError("This feedback isn't waiting for review.")
    if action in ("approve", "discard"):
        if action == "approve" and not (row.guardrail and row.guardrail["passed"]):
            raise ConflictError("Fix the problems the checks found before approving.")
        with graph_factory() as graph:
            run(db, feedback_id, graph, decision={"action": action, "user_id": user.id})
        db.refresh(row)
        return row, None
    if action == "edit":
        subject = " ".join((fields.get("subject") or "").split())
        body = (fields.get("body") or "").strip()
        if not subject or not body:
            raise InvalidInputError("The subject and the letter can't be empty.")
        if len(subject) > MAX_SUBJECT or len(body) > MAX_BODY:
            raise InvalidInputError("The letter is too long.")
        row.pending_edit = {"action": "edit", "subject": subject, "body": body,
                            "user_id": user.id}  # fmt: skip
    elif action == "regenerate":
        instruction = " ".join((fields.get("instruction") or "").split())[:300] or None
        row.pending_edit = {"action": "regenerate", "instruction": instruction,
                            "user_id": user.id}  # fmt: skip
    else:
        raise InvalidInputError("Choose approve, edit, regenerate or discard.")
    row.status = "drafting"
    _event(row, f"{action}_requested", user.id)
    db.commit()
    return row, _enqueue_run(row, resume=True)


# --- sending ---------------------------------------------------------------------------


def _signature() -> str:
    lines = [
        part
        for part in (
            settings.email_sender_name,
            settings.email_sender_title,
            settings.email_sender_company,
        )
        if part
    ]
    return "\n".join(lines) or "The hiring team"


def _filled(row: CandidateFeedback, name: str) -> tuple[str, str]:
    body = row.draft["body"].replace("{candidate_name}", name).replace("{signature}", _signature())
    return row.draft["subject"], body


def _html(body: str) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 16px">{html.escape(p).replace(chr(10), "<br>")}</p>'
        for p in body.split("\n\n")
    )
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;'
        f'color:#17211f;max-width:560px">{paragraphs}</div>'
    )


def _tools(db: Session) -> Any:
    from app.tools.mcp_client import McpTools

    token = None
    account = integrations.scheduling_account(db)
    if account:
        try:
            token = integrations.access_token(db, IntegrationProvider.GOOGLE)[0]
        except AppError:
            token = None
    return McpTools(token, integrations.mail_from(account) if token else None)


def outbox_dir() -> Path:
    return Path(settings.storage_dir) / "outbox"


def _write_outbox(row: CandidateFeedback, to: str | None, subject: str, body: str) -> Path:
    msg = EmailMessage()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to or "(no email address on file)"
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_alternative(_html(body), subtype="html")
    folder = outbox_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"feedback-{row.id}.eml"
    path.write_bytes(msg.as_bytes())
    return path


def send(db: Session, feedback_id: int, user: User) -> CandidateFeedback:
    row = _get(db, feedback_id)
    # claimed in one statement, so two presses can't both send it
    claimed = db.execute(
        update(CandidateFeedback)
        .where(CandidateFeedback.id == feedback_id, CandidateFeedback.status == "approved")
        .values(status="sending")
    ).rowcount
    db.commit()
    db.refresh(row)
    if not claimed:
        if row.status in ("sending", "sent"):
            raise ConflictError("This feedback has already been sent.")
        raise ConflictError("Feedback is sent only after a person approves it.")

    candidate = db.get(Candidate, row.candidate_id)
    subject, body = _filled(row, candidate.full_name)
    channel, message_id, path = None, None, None
    if candidate.email:
        try:
            tools = _tools(db)
            tool = "gmail_send" if tools.channel == "google" else "smtp_send"
            sent = tools.call(
                tool,
                {"to": candidate.email, "subject": subject, "body": body, "html": _html(body)},
            )
            channel = "gmail" if tool == "gmail_send" else "smtp"
            message_id = sent.get("message_id")
        except Exception as exc:
            # the letter is approved; a dead email service mustn't lose it
            log.warning(
                "candidate_feedback_email_failed", feedback_id=row.id, error_type=type(exc).__name__
            )
    if channel is None:
        try:
            path = _write_outbox(row, candidate.email, subject, body)
        except OSError as exc:
            row.status = "approved"
            db.commit()
            raise ExternalServiceError(
                "The email couldn't be sent or saved. Please try again."
            ) from exc
        channel = "outbox"
    row.status, row.sent_at, row.send_channel = "sent", datetime.now(UTC), channel
    row.message_id = (message_id or "")[:300] or None
    row.outbox_path = str(path) if path else None
    _event(row, "sent", user.id, channel=channel)
    db.commit()
    return row


# --- reading ---------------------------------------------------------------------------


def _evidence(db: Session, ref: dict[str, Any]) -> dict[str, Any]:
    # what a stored reference points at, for the reviewer to check
    kind, ref_id = ref["type"], ref["id"]
    out: dict[str, Any] = {"type": kind, "id": ref_id, "found": False}
    if kind == "screening_result":
        r = db.get(ScreeningRequirementResult, ref_id)
        if r:
            out.update(found=True, status=r.status.value, quote=r.evidence_quote,
                       verified=r.evidence_verified, text=r.reasoning)  # fmt: skip
    elif kind == "qa_pair":
        p = db.get(QaPair, ref_id)
        if p:
            out.update(found=True, sequence=p.sequence, question=p.question_text,
                       text=p.justification, interview_id=p.interview_id)  # fmt: skip
    elif kind == "assessment_finding":
        f = db.get(AssessmentFinding, ref_id)
        if f:
            out.update(found=True, kind=f.kind.value, text=f.text)
    elif kind == "score_dimension":
        d = db.get(ScoreDimension, ref_id)
        if d:
            out.update(found=True, dimension=d.dimension.value, text=d.justification)
    return out


def _summary(db: Session, row: CandidateFeedback) -> dict[str, Any]:
    candidate = db.get(Candidate, row.candidate_id)
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "candidate_name": candidate.full_name if candidate else "",
        "job_id": row.job_id,
        "interview_id": row.interview_id,
        "stage": row.stage,
        "outcome": row.outcome,
        "status": row.status,
        "message": row.message,
        "fallback_reason": row.fallback_reason,
        "guardrail_passed": bool(row.guardrail and row.guardrail.get("passed")),
        "marked_at": row.marked_at,
        "approved_at": row.approved_at,
        "sent_at": row.sent_at,
        "send_channel": row.send_channel,
        "updated_at": row.updated_at,
    }


def detail(db: Session, feedback_id: int) -> dict[str, Any]:
    row = _get(db, feedback_id)
    out = _summary(db, row)
    out["job_title"] = get_job(db, row.job_id).title
    requirements = {r.id: r.text for r in get_job(db, row.job_id).requirements}
    out["points"] = [
        {
            **p,
            "requirement": requirements.get(p["requirement_id"], p["requirement"]),
            "evidence_ref": _evidence(db, p["evidence_ref"]),
            "support": [_evidence(db, s) for s in p.get("support") or []],
        }
        for p in row.points or []
    ]
    out["draft"] = row.draft
    out["guardrail"] = row.guardrail
    out["rule_labels"] = RULES
    out["notes"] = row.notes or []
    out["excluded_notes"] = row.excluded_notes or []
    out["fallback_message"] = FALLBACK_LABEL.get(row.fallback_reason or "")
    out["warnings"] = row.warnings or []
    out["events"] = row.events or []
    out["model_name"] = row.model_name
    out["has_email"] = bool(db.get(Candidate, row.candidate_id).email)
    return out


def for_interview(db: Session, interview_id: int) -> dict[str, Any]:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    row = _row(db, interview.candidate_id, interview.job_id, "interview")
    assessment = db.scalar(select(Assessment).where(Assessment.interview_id == interview_id))
    return {
        "interview_id": interview.id,
        "candidate_id": interview.candidate_id,
        "job_id": interview.job_id,
        "assessment_ready": bool(assessment and assessment.status == "assessed"),
        "feedback": detail(db, row.id) if row else None,
    }


def job_queue(db: Session, job_id: int, stage: str | None = None) -> list[dict[str, Any]]:
    get_job(db, job_id)
    stmt = select(CandidateFeedback).where(CandidateFeedback.job_id == job_id)
    if stage:
        stmt = stmt.where(CandidateFeedback.stage == stage)
    rows = db.scalars(stmt.order_by(CandidateFeedback.id)).all()
    return [_summary(db, r) for r in rows]
