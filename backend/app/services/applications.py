# pulls google form responses in as candidates, applications and cv documents.
# each response is committed on its own, so one bad response is reported and the
# rest still come in

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.errors import AppError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.candidate import Application, Candidate, CvDocument
from app.models.enums import ExtractionStatus, IntegrationProvider
from app.models.job import JobPost
from app.services import integrations
from app.services.jobs import get_job
from app.tools import google_forms
from app.tools.google_forms import FormResponse, Question

log = get_logger(__name__)

ProgressFn = Callable[..., None]

CV_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

# question title keywords -> field. first match wins, so order matters
FIELD_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("consent", ("i agree", "consent")),
    ("linkedin", ("linkedin",)),
    ("email", ("email", "e-mail")),
    ("phone", ("phone", "mobile")),
    ("experience", ("experience",)),
    ("location", ("location", "city", "where are you")),
    ("cover_note", ("cover", "note", "message", "why")),
    ("full_name", ("full name", "name")),
]


@dataclass
class FieldMap:
    fields: dict[str, str]  # field -> question id
    cv_question: str | None


def map_questions(questions: list[Question]) -> FieldMap:
    fields: dict[str, str] = {}
    cv_question = None
    for q in questions:
        if q.kind == "file":
            cv_question = cv_question or q.question_id
            continue
        title = q.title.lower()
        for name, words in FIELD_KEYWORDS:
            if name not in fields and any(w in title for w in words):
                fields[name] = q.question_id
                break
    return FieldMap(fields, cv_question)


def sync_applications(db: Session, job_id: int, progress: ProgressFn | None = None) -> dict:
    job = get_job(db, job_id)
    posts = [p for p in job.posts if p.form_id]
    if not posts:
        raise InvalidInputError("This job has no application form yet. Prepare the post first.")
    token, _ = integrations.access_token(db, IntegrationProvider.GOOGLE)

    imported = existing = 0
    failed: list[dict[str, Any]] = []
    for post in posts:
        _, questions = google_forms.get_form(token, post.form_id)
        mapping = map_questions(questions)
        responses = google_forms.list_responses(token, post.form_id)
        known = set(
            db.scalars(select(Application.external_response_id).where(Application.job_id == job_id))
        )
        new = [r for r in responses if r.response_id not in known]
        existing += len(responses) - len(new)

        for index, response in enumerate(new):
            if progress:
                progress(
                    state="running",
                    message=f"Importing application {index + 1} of {len(new)}.",
                    total=len(new),
                    completed=index,
                    failed_items=failed,
                )
            try:
                _import(db, token, job_id, post, mapping, response)
                imported += 1
            except AppError as exc:
                db.rollback()
                failed.append({"item_ref": response.response_id, "reason": exc.message})
                log.info("application_import_failed", job_id=job_id, response_ref=index)

        post.last_synced_at = datetime.now(UTC)
        db.commit()

    log.info(
        "applications_synced",
        job_id=job_id,
        imported=imported,
        already_imported=existing,
        failed=len(failed),
    )
    return {
        "job_id": job_id,
        "imported": imported,
        "already_imported": existing,
        "failed": len(failed),
        "failed_items": failed,
    }


def _import(
    db: Session,
    token: str,
    job_id: int,
    post: JobPost,
    mapping: FieldMap,
    response: FormResponse,
) -> None:
    def answer(field: str) -> str | None:
        qid = mapping.fields.get(field)
        values = response.text.get(qid, []) if qid else []
        value = " ".join(v.strip() for v in values if v.strip())
        return value or None

    email = (response.respondent_email or answer("email") or "").strip().lower()
    if not email or "@" not in email:
        raise InvalidInputError("The response has no email address.")
    name = answer("full_name")
    if not name:
        raise InvalidInputError("The response has no name.")
    if mapping.fields.get("consent") and not answer("consent"):
        raise InvalidInputError("The applicant did not agree to their data being processed.")

    candidate = db.scalar(select(Candidate).where(func.lower(Candidate.email) == email))
    if candidate is None:
        candidate = Candidate(full_name=name[:200], email=email)
        db.add(candidate)
    candidate.phone = (answer("phone") or candidate.phone or "")[:50] or None
    candidate.location = (answer("location") or candidate.location or "")[:160] or None
    db.flush()

    application = Application(
        job_id=job_id,
        candidate_id=candidate.id,
        job_post_id=post.id,
        source="google_form",
        cover_note=answer("cover_note"),
        submitted_at=_parse_time(response.submitted_at),
        external_response_id=response.response_id,
        answers={
            k: v
            for k, v in {
                "years_experience": answer("experience"),
                "linkedin_url": answer("linkedin"),
            }.items()
            if v
        },
        consent_given=bool(answer("consent")) or "consent" not in mapping.fields,
    )
    db.add(application)
    db.flush()

    files = response.files.get(mapping.cv_question, []) if mapping.cv_question else []
    if not files:
        raise InvalidInputError("The response has no CV attached.")
    application.cv_document_id = _download_cv(db, token, job_id, candidate.id, files[0]).id
    db.commit()


def _download_cv(
    db: Session, token: str, job_id: int, candidate_id: int, file: dict[str, str]
) -> CvDocument:
    meta = google_forms.file_metadata(token, file["fileId"])
    mime = meta.get("mimeType") or file.get("mimeType", "")
    name = meta.get("name") or file.get("fileName") or "cv"
    suffix = CV_TYPES.get(mime) or Path(name).suffix.lower()
    if suffix not in settings.allowed_cv_extension_list:
        raise InvalidInputError("The CV is not a PDF or Word (.docx) file.")
    size = int(meta.get("size") or 0)
    if size > settings.max_upload_bytes:
        raise InvalidInputError(f"The CV is larger than {settings.max_upload_mb} MB.")

    data = google_forms.download_file(token, file["fileId"], settings.max_upload_bytes)
    folder = Path(settings.storage_dir) / "cvs"
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(data)

    document = CvDocument(
        candidate_id=candidate_id,
        job_id=job_id,
        original_filename=Path(name).name[:300],
        storage_path=str(stored),
        content_type=mime or "application/octet-stream",
        size_bytes=len(data),
        status=ExtractionStatus.PENDING,
    )
    db.add(document)
    db.flush()
    return document


def list_applications(db: Session, job_id: int) -> list[dict[str, Any]]:
    get_job(db, job_id)
    rows = db.scalars(
        select(Application)
        .where(Application.job_id == job_id)
        .options(selectinload(Application.candidate))
        .order_by(Application.submitted_at.desc().nulls_last(), Application.id.desc())
    )
    out = []
    for app in rows:
        cv = db.get(CvDocument, app.cv_document_id) if app.cv_document_id else None
        out.append(
            {
                "id": app.id,
                "candidate_id": app.candidate_id,
                "full_name": app.candidate.full_name,
                "email": app.candidate.email,
                "phone": app.candidate.phone,
                "location": app.candidate.location,
                "source": app.source,
                "submitted_at": app.submitted_at,
                "years_experience": (app.answers or {}).get("years_experience"),
                "linkedin_url": (app.answers or {}).get("linkedin_url"),
                "cover_note": app.cover_note,
                "cv_filename": cv.original_filename if cv else None,
                "cv_status": cv.status.value if cv else None,
            }
        )
    return out


def cv_file(db: Session, application_id: int) -> CvDocument:
    app = db.get(Application, application_id)
    if app is None or not app.cv_document_id:
        raise NotFoundError("That CV is not available.")
    cv = db.get(CvDocument, app.cv_document_id)
    if cv is None or not Path(cv.storage_path).exists():
        raise NotFoundError("That CV is not available.")
    return cv


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
