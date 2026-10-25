# face identification at interview and the profile shown once identity is settled.
#
# the reference is the photo an applicant sent with the form (if they agreed) or a photo
# they take on their interview page. either way only an encrypted template is kept, never
# the picture, and it is deleted a few days after the interview. a live face is only looked
# at when the candidate the link belongs to said yes. the match never decides on its own
# that someone is who they claim: a weak or missing match says so, and the interviewer
# confirms or rejects

import base64
import hashlib
import struct
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.candidate import CvExtraction
from app.models.enums import InterviewStatus
from app.models.identity import BiometricConsent, FaceReference, IdentityCheck
from app.models.interview import Interview
from app.models.screening import CandidateScore, Screening
from app.models.user import User
from app.tools import face

log = get_logger(__name__)

CONSENT_VERSION = "face-v1"
MAX_SELFIE_BYTES = 2 * 1024 * 1024
MAX_SELFIES = 5
RETRY_KEY = "identity:retry:{}"

Embedder = Callable[[bytes], face.Face]
DriveDelete = Callable[[str], None]

SETTLED = ("confirmed", "manually_confirmed")

# shown to the interviewer next to each status; the candidate never sees these
EXPLAIN = {
    "confirmed": "The face matches the photo on file for this candidate.",
    "low_confidence": "The face looks similar to the photo on file, but not enough to be sure.",
    "mismatch": "The face matches a different candidate's photo better than this one's.",
    "manually_confirmed": "You confirmed who this is.",
    "rejected": "You said this is not the candidate.",
}
NOT_IDENTIFIED = {
    "no_consent": "The candidate didn't agree to face matching.",
    "no_reference": "There is no usable photo on file for this candidate.",
    "no_face": "No face could be seen on the candidate's camera.",
    "several_faces": "More than one face was on the candidate's camera.",
    "too_small": "The face on the candidate's camera was too small or far away.",
    "no_match": "The face didn't match the photo on file.",
    "no_video": "The candidate's camera is off.",
}
SELFIE_PROBLEM = {
    "no_face": "We couldn't see a face. Face the camera in good light and try again.",
    "several_faces": "More than one face is in view. Make sure only you are in the picture.",
    "too_small": "Your face is too small in the picture. Move closer to the camera.",
    "unreadable": "The picture couldn't be read. Try again.",
}


def consent_text() -> str:
    return (
        "IntelliHire can check that the person joining this interview is you by comparing "
        "your camera image with a photo of you. Only a numeric face template is kept, "
        "encrypted, never the photo itself, and it is deleted "
        f"{settings.face_reference_keep_days} days after your interview. Saying no changes "
        "nothing about how your application is assessed: the interviewer will simply confirm "
        "who you are on the call. You can withdraw at any time on this page."
    )


# --- the template, encrypted -----------------------------------------------------------


def _fernet() -> Fernet:
    digest = hashlib.sha256(b"intellihire-biometrics:" + settings.jwt_secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _pack(vector: list[float]) -> bytes:
    return _fernet().encrypt(struct.pack(f"<{len(vector)}f", *vector))


def _unpack(blob: bytes) -> list[float]:
    raw = _fernet().decrypt(blob)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


# --- google drive, where form photos wait ----------------------------------------------


def drive_deleter(db: Session) -> DriveDelete | None:
    # none when google isn't connected; the photo then waits for the sweep
    from app.models.enums import IntegrationProvider
    from app.services import integrations
    from app.tools import google_forms

    try:
        token, _ = integrations.access_token(db, IntegrationProvider.GOOGLE)
    except Exception:
        return None
    return lambda file_id: google_forms.delete_file(token, file_id)


def drive_downloader(db: Session) -> Callable[[str], bytes]:
    from app.models.enums import IntegrationProvider
    from app.services import integrations
    from app.tools import google_forms

    token, _ = integrations.access_token(db, IntegrationProvider.GOOGLE)
    return lambda file_id: google_forms.download_file(token, file_id, MAX_SELFIE_BYTES * 5)


# --- consent and the reference ---------------------------------------------------------


def latest_consent(db: Session, candidate_id: int) -> str | None:
    row = db.scalar(
        select(BiometricConsent)
        .where(BiometricConsent.candidate_id == candidate_id)
        .order_by(BiometricConsent.id.desc())
    )
    return row.decision if row else None


def _record(db: Session, candidate_id: int, decision: str, source: str) -> None:
    db.add(
        BiometricConsent(
            candidate_id=candidate_id,
            decision=decision,
            source=source,
            text_version=CONSENT_VERSION,
        )
    )


def _reference(db: Session, candidate_id: int) -> FaceReference | None:
    return db.scalar(select(FaceReference).where(FaceReference.candidate_id == candidate_id))


def _drop_reference(db: Session, candidate_id: int, delete_drive: DriveDelete | None) -> None:
    ref = _reference(db, candidate_id)
    if ref is None:
        return
    if ref.drive_file_id and delete_drive:
        try:
            delete_drive(ref.drive_file_id)
        except Exception:
            # the sweep can't retry without the id, so keep the row for it
            log.warning("face_photo_not_deleted", candidate_id=candidate_id)
            ref.status, ref.embedding, ref.expires_at = "pending", None, datetime.now(UTC)
            return
    db.delete(ref)


def record_form_photo(
    db: Session,
    candidate_id: int,
    drive_file_id: str | None,
    answered: bool,
    consented: bool,
    delete_drive: DriveDelete,
) -> None:
    # called by the application intake. nothing is downloaded here: the photo stays where
    # google put it until an interview is booked, and a photo sent without consent is
    # deleted unused
    if answered:
        _record(db, candidate_id, "given" if consented else "declined", "form")
    if not drive_file_id:
        return
    if not consented:
        try:
            delete_drive(drive_file_id)
        except Exception:
            log.warning("face_photo_not_deleted", candidate_id=candidate_id)
        return
    ref = _reference(db, candidate_id)
    if ref is not None and ref.drive_file_id and ref.drive_file_id != drive_file_id:
        try:
            delete_drive(ref.drive_file_id)
        except Exception:
            log.warning("face_photo_not_deleted", candidate_id=candidate_id)
    if ref is None:
        ref = FaceReference(candidate_id=candidate_id, source="form")
        db.add(ref)
    ref.status, ref.source, ref.reason, ref.embedding = "pending", "form", None, None
    ref.drive_file_id = drive_file_id
    ref.expires_at = datetime.now(UTC) + timedelta(days=settings.face_photo_max_days)


def needs_preparing(db: Session, candidate_id: int) -> bool:
    ref = _reference(db, candidate_id)
    return bool(ref and ref.status == "pending" and ref.drive_file_id)


def keep_until(db: Session, candidate_id: int, when: datetime) -> None:
    ref = _reference(db, candidate_id)
    if ref is not None and ref.status != "pending":
        ref.expires_at = when + timedelta(days=settings.face_reference_keep_days)


def prepare_reference(
    db: Session,
    candidate_id: int,
    download: Callable[[str], bytes],
    delete_drive: DriveDelete,
    embedder: Embedder | None = None,
    keep_from: datetime | None = None,
) -> str:
    # an interview was booked: turn the form photo into a template, then delete the photo
    ref = _reference(db, candidate_id)
    if ref is None or ref.status != "pending" or not ref.drive_file_id:
        return ref.status if ref else "none"
    if latest_consent(db, candidate_id) != "given":
        _drop_reference(db, candidate_id, delete_drive)
        db.commit()
        return "no_consent"
    result = (embedder or face.embed_image)(download(ref.drive_file_id))
    delete_drive(ref.drive_file_id)
    ref.drive_file_id = None
    if result.vector is None:
        ref.status, ref.reason, ref.embedding = "unusable", result.reason, None
    else:
        ref.status, ref.reason = "ready", None
        ref.embedding = _pack(result.vector)
        ref.model_name = face.MODEL_NAME
    ref.expires_at = (keep_from or datetime.now(UTC)) + timedelta(
        days=settings.face_reference_keep_days
    )
    db.commit()
    log.info("face_reference_prepared", candidate_id=candidate_id, status=ref.status)
    return ref.status


# --- the candidate's interview page ----------------------------------------------------


def _link_interview(db: Session, link_token: str) -> Interview:
    from app.services.interviews import by_link

    interview = by_link(db, link_token)
    if interview.status not in (InterviewStatus.SCHEDULED, InterviewStatus.IN_PROGRESS):
        raise ConflictError("This interview is no longer open.")
    return interview


def link_status(db: Session, link_token: str) -> dict[str, Any]:
    interview = _link_interview(db, link_token)
    ref = _reference(db, interview.candidate_id)
    return {
        "consent": latest_consent(db, interview.candidate_id),
        "reference": ref.status if ref else None,
        "source": ref.source if ref else None,
        "text": consent_text(),
        "version": CONSENT_VERSION,
    }


def enrol_selfie(
    db: Session,
    link_token: str,
    images: list[bytes],
    embedder: Embedder | None = None,
    delete_drive: DriveDelete | None = None,
) -> dict[str, Any]:
    interview = _link_interview(db, link_token)
    if not images or len(images) > MAX_SELFIES:
        raise InvalidInputError("Send between one and five pictures.")
    if any(len(i) > MAX_SELFIE_BYTES for i in images):
        raise InvalidInputError("A picture is too large.")
    results = [(embedder or face.embed_image)(i) for i in images]
    del images
    vectors = [r.vector for r in results if r.vector is not None]
    if not vectors:
        reasons = [r.reason for r in results if r.reason]
        raise InvalidInputError(
            SELFIE_PROBLEM.get(reasons[0] if reasons else "", SELFIE_PROBLEM["unreadable"])
        )
    candidate_id = interview.candidate_id
    _record(db, candidate_id, "given", "join_page")
    ref = _reference(db, candidate_id)
    if ref is not None and ref.drive_file_id and delete_drive:
        try:
            delete_drive(ref.drive_file_id)
        except Exception:
            log.warning("face_photo_not_deleted", candidate_id=candidate_id)
    if ref is None:
        ref = FaceReference(candidate_id=candidate_id, source="selfie")
        db.add(ref)
    ref.status, ref.source, ref.reason, ref.drive_file_id = "ready", "selfie", None, None
    ref.embedding = _pack(face.average(vectors))
    ref.model_name = face.MODEL_NAME
    ref.expires_at = (interview.scheduled_end or datetime.now(UTC)) + timedelta(
        days=settings.face_reference_keep_days
    )
    db.commit()
    log.info("face_reference_enrolled", candidate_id=candidate_id, frames=len(vectors))
    return link_status(db, link_token)


def refuse(
    db: Session, link_token: str, decision: str, delete_drive: DriveDelete | None = None
) -> dict[str, Any]:
    # declined before ever agreeing, or withdrawn later: either way the template goes
    interview = _link_interview(db, link_token)
    _record(db, interview.candidate_id, decision, "join_page")
    _drop_reference(db, interview.candidate_id, delete_drive)
    db.commit()
    log.info("biometric_consent_refused", candidate_id=interview.candidate_id, decision=decision)
    return link_status(db, link_token)


# --- matching --------------------------------------------------------------------------


def decide(claimed: list[float], others: list[list[float]], live: list[float]) -> dict:
    score = face.similarity(claimed, live)
    best_other = max((face.similarity(o, live) for o in others), default=None)
    margin = round(score - best_other, 3) if best_other is not None else None
    high, low, gap = (
        settings.face_match_threshold,
        settings.face_low_threshold,
        settings.face_match_margin,
    )
    out = {"score": round(score, 3), "margin": margin, "reason": None}
    if best_other is not None and best_other >= high and best_other - score >= gap:
        return {**out, "status": "mismatch", "reason": "other"}
    if score >= high and (margin is None or margin >= gap):
        return {**out, "status": "confirmed"}
    if score >= low:
        return {**out, "status": "low_confidence"}
    return {**out, "status": "not_identified", "reason": "no_match"}


def _save(db: Session, interview_id: int, **fields: Any) -> IdentityCheck:
    row = IdentityCheck(interview_id=interview_id, **fields)
    db.add(row)
    db.commit()
    log.info("identity_checked", interview_id=interview_id, status=row.status)
    return row


def references_for(db: Session, interview_id: int) -> tuple[list[float], list[list[float]]] | None:
    # the claimed candidate's template and everyone else's that is usable, or None (with a
    # check saved) when the live face must not be looked at
    interview = db.get(Interview, interview_id)
    if interview is None:
        return None
    if latest_consent(db, interview.candidate_id) != "given":
        _save(db, interview_id, status="not_identified", method="face", reason="no_consent")
        return None
    now = datetime.now(UTC)
    rows = db.scalars(
        select(FaceReference).where(
            FaceReference.status == "ready", FaceReference.embedding.is_not(None)
        )
    ).all()
    claimed, others = None, []
    for r in rows:
        expires = r.expires_at if r.expires_at is None or r.expires_at.tzinfo else (
            r.expires_at.replace(tzinfo=UTC)
        )  # fmt: skip
        if expires is not None and expires < now:
            continue
        if r.candidate_id == interview.candidate_id:
            claimed = _unpack(r.embedding)
        elif latest_consent(db, r.candidate_id) == "given":
            others.append(_unpack(r.embedding))
    if claimed is None:
        _save(db, interview_id, status="not_identified", method="face", reason="no_reference")
        return None
    return claimed, others


def record_match(
    db: Session,
    interview_id: int,
    live: list[face.Face],
    refs: tuple[list[float], list[list[float]]] | None = None,
) -> IdentityCheck | None:
    # live holds what the transcriber saw on the candidate's camera, frame by frame
    refs = refs or references_for(db, interview_id)
    if refs is None:
        return current_row(db, interview_id)
    vectors = [f.vector for f in live if f.vector is not None]
    if not vectors:
        reasons = [f.reason for f in live if f.reason]
        reason = max(set(reasons), key=reasons.count) if reasons else "no_video"
        return _save(db, interview_id, status="not_identified", method="face", reason=reason)
    result = decide(refs[0], refs[1], face.average(vectors))
    return _save(db, interview_id, method="face", **result)


def request_retry(db: Session, interview_id: int, user: User) -> None:
    from app.worker.queue import get_redis

    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    if interview.status != InterviewStatus.IN_PROGRESS:
        raise ConflictError("Identity can only be checked while the interview is running.")
    get_redis().set(RETRY_KEY.format(interview_id), "1", ex=120)


def take_retry(interview_id: int) -> bool:
    from app.worker.queue import get_redis

    return bool(get_redis().delete(RETRY_KEY.format(interview_id)))


def decide_manually(db: Session, interview_id: int, user: User, decision: str) -> dict:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    status = {"confirm": "manually_confirmed", "reject": "rejected"}.get(decision)
    if status is None:
        raise InvalidInputError("Choose confirm or reject.")
    _save(db, interview_id, status=status, method="manual", decided_by_id=user.id)
    return current(db, interview_id)


def current_row(db: Session, interview_id: int) -> IdentityCheck | None:
    return db.scalar(
        select(IdentityCheck)
        .where(IdentityCheck.interview_id == interview_id)
        .order_by(IdentityCheck.id.desc())
    )


def current(db: Session, interview_id: int) -> dict[str, Any]:
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    row = current_row(db, interview_id)
    ref = _reference(db, interview.candidate_id)
    base = {
        "consent": latest_consent(db, interview.candidate_id),
        "reference": ref.status if ref else None,
        "profile_available": bool(row and row.status in SETTLED),
    }
    if row is None:
        return {**base, "status": "pending", "message": "Waiting for the candidate's camera."}
    message = (
        NOT_IDENTIFIED.get(row.reason or "", "The candidate couldn't be identified.")
        if row.status == "not_identified"
        else EXPLAIN.get(row.status, "")
    )
    return {
        **base,
        "status": row.status,
        "method": row.method,
        "reason": row.reason,
        "score": row.score,
        "margin": row.margin,
        "message": message,
        "checked_at": row.created_at
        if row.created_at.tzinfo
        else row.created_at.replace(tzinfo=UTC),  # fmt: skip
    }


# --- the profile, once identity is settled --------------------------------------------


def profile(db: Session, interview_id: int) -> dict[str, Any]:
    from app.agents.copilot.checks import plan_targets
    from app.models.candidate import Candidate
    from app.services.jobs import get_job

    interview = db.get(Interview, interview_id)
    if interview is None:
        raise NotFoundError("That interview does not exist.")
    row = current_row(db, interview_id)
    if row is None or row.status not in SETTLED:
        raise ConflictError(
            "The profile opens once the candidate's identity is confirmed, by the face match "
            "or by you."
        )
    job = get_job(db, interview.job_id)
    candidate = db.get(Candidate, interview.candidate_id)
    screening = db.scalar(
        select(Screening).where(
            Screening.job_id == job.id, Screening.candidate_id == interview.candidate_id
        )
    )
    structured: dict[str, Any] = {}
    if screening and screening.cv_document_id:
        extraction = db.scalar(
            select(CvExtraction).where(CvExtraction.cv_document_id == screening.cv_document_id)
        )
        structured = (extraction.structured or {}) if extraction else {}
    results = {r.job_requirement_id: r for r in screening.requirement_results} if screening else {}
    score = db.scalar(
        select(CandidateScore).where(
            CandidateScore.job_id == job.id, CandidateScore.candidate_id == interview.candidate_id
        )
    )
    requirements = [
        {"id": r.id, "text": r.text, "category": r.category.value, "is_mandatory": r.is_mandatory}
        for r in job.requirements
    ]
    probes = plan_targets(
        requirements,
        {
            str(rid): {"status": r.status.value, "evidence_quote": r.evidence_quote}
            for rid, r in results.items()
        },
        None,
    )
    roles = structured.get("work_history") or []
    met = [
        {"text": req["text"], "quote": results[req["id"]].evidence_quote}
        for req in requirements
        if req["id"] in results
        and results[req["id"]].status.value == "met"
        and results[req["id"]].evidence_verified
    ]
    return {
        "name": candidate.full_name if candidate else "",
        "headline": structured.get("headline") or "",
        "years_experience": structured.get("total_years_experience"),
        "current_role": (
            {"title": roles[0].get("title", ""), "organisation": roles[0].get("organisation", "")}
            if roles
            else None
        ),
        "skills": (structured.get("skills") or [])[:10],
        "requirements_met": met[:6],
        "score": score.aggregate_score if score else None,
        "rank": score.rank if score else None,
        "probe": [
            {
                "text": p["text"],
                "must_have": p["must_have"],
                "screening": p["screening"],
                "cv_quote": p["cv_quote"],
            }
            for p in probes
            if p["screening"] != "met"
        ][:4],
        "summary": screening.summary if screening else None,
    }


# --- retention -------------------------------------------------------------------------


def after_end(db: Session, interview: Interview) -> None:
    # the countdown to deletion starts when the interview ends; never stops it ending
    try:
        keep_until(db, interview.candidate_id, datetime.now(UTC))
        db.commit()
    except Exception:
        log.warning("face_reference_expiry_not_set", interview_id=interview.id)
        db.rollback()


def sweep(db: Session, delete_drive: DriveDelete | None) -> int:
    # deletes every template and waiting photo past its date
    now = datetime.now(UTC)
    removed = 0
    for ref in db.scalars(select(FaceReference)).all():
        expires = ref.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires is None or expires > now:
            continue
        if ref.drive_file_id:
            if delete_drive is None:
                continue
            try:
                delete_drive(ref.drive_file_id)
            except Exception:
                log.warning("face_photo_not_deleted", candidate_id=ref.candidate_id)
                continue
        db.delete(ref)
        removed += 1
    db.commit()
    if removed:
        log.info("face_references_expired", removed=removed)
    return removed


def delete_for_candidate(db: Session, candidate_id: int, delete_drive: DriveDelete | None) -> None:
    # part of deleting a candidate: the waiting photo in drive, then the rows
    ref = _reference(db, candidate_id)
    if ref is not None and ref.drive_file_id and delete_drive:
        try:
            delete_drive(ref.drive_file_id)
        except Exception:
            log.warning("face_photo_not_deleted", candidate_id=candidate_id)
    db.execute(delete(FaceReference).where(FaceReference.candidate_id == candidate_id))
    db.execute(delete(BiometricConsent).where(BiometricConsent.candidate_id == candidate_id))
