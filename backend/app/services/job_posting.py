# advertising a job: copy the google form template for it, draft the linkedin post,
# let the interviewer edit it, publish it, and later close applications

import re
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import AppError, ConflictError, InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.enums import IntegrationProvider, JobStatus, PostStatus
from app.models.job import Job, JobPost
from app.services import integrations
from app.services.jobs import get_job
from app.services.post_writer import draft_post
from app.tools import google_forms, linkedin
from app.tools.http import ProviderError

log = get_logger(__name__)

ProgressFn = Callable[..., None]
ACTIVE = (PostStatus.PREPARING, PostStatus.DRAFT, PostStatus.POSTED)


def current_post(db: Session, job_id: int) -> JobPost | None:
    return db.scalar(
        select(JobPost).where(JobPost.job_id == job_id).order_by(JobPost.id.desc()).limit(1)
    )


def _post_for(db: Session, job_id: int) -> JobPost:
    post = current_post(db, job_id)
    if post is None:
        raise NotFoundError("This job has not been prepared for posting yet.")
    return post


def start_post(db: Session, job_id: int, user_id: int) -> JobPost:
    job = get_job(db, job_id)
    if not job.description_md:
        raise InvalidInputError("Write the job description first; the post is built from it.")
    if not settings.google_form_template_id:
        raise InvalidInputError(
            "No Google Form template is set. Add GOOGLE_FORM_TEMPLATE_ID to .env "
            "(see PROJECT/integrations-setup.md)."
        )
    # fails early with "connect google" instead of inside the background job
    integrations.access_token(db, IntegrationProvider.GOOGLE)

    existing = current_post(db, job_id)
    if existing is not None and existing.status in ACTIVE:
        raise ConflictError("This job already has a post. Close it before preparing a new one.")

    post = JobPost(
        job_id=job.id, channel="linkedin", status=PostStatus.PREPARING, created_by_id=user_id
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    log.info("job_post_started", job_id=job.id, post_id=post.id)
    return post


def prepare_post(db: Session, post_id: int, progress: ProgressFn | None = None) -> JobPost:
    def report(message: str, done: int) -> None:
        if progress:
            progress(state="running", message=message, total=4, completed=done)

    post = db.get(JobPost, post_id)
    if post is None:
        raise NotFoundError("That post no longer exists.")
    job = get_job(db, post.job_id)

    try:
        token, _ = integrations.access_token(db, IntegrationProvider.GOOGLE)
        if not post.form_id:
            report("Copying the application form template.", 0)
            post.form_id = google_forms.copy_form(
                token, settings.google_form_template_id, f"Application: {job.title}"
            )
            db.commit()

        report("Adding the job details to the form.", 1)
        google_forms.update_form_info(
            token, post.form_id, f"Apply: {job.title}", _form_description(job)
        )
        try:
            google_forms.set_accepting_responses(token, post.form_id, True)
        except ProviderError as exc:
            # older forms don't support publish settings, they accept responses already
            log.info("form_publish_settings_skipped", post_id=post.id, status=exc.status)

        ref, questions = google_forms.get_form(token, post.form_id)
        if not any(q.kind == "file" for q in questions):
            raise InvalidInputError(
                "The template form has no file upload question for the CV. Add one in Google "
                "Forms (see PROJECT/integrations-setup.md) and prepare the post again."
            )
        post.form_url = ref.responder_url
        post.form_edit_url = ref.edit_url

        report("Drafting the LinkedIn post.", 2)
        text, tags = draft_post(job)
        post.rendered_body = text
        post.hashtags = tags
        post.status = PostStatus.DRAFT
        post.failure_reason = None
        db.commit()
    except AppError as exc:
        db.rollback()
        post = db.get(JobPost, post_id)
        post.status = PostStatus.FAILED
        post.failure_reason = exc.message[:500]
        db.commit()
        log.info("job_post_prepare_failed", post_id=post_id)
        raise

    log.info("job_post_prepared", job_id=job.id, post_id=post.id)
    return post


def update_text(db: Session, job_id: int, text: str) -> JobPost:
    post = _post_for(db, job_id)
    if post.status != PostStatus.DRAFT:
        raise ConflictError("Only a draft post can be edited.")
    post.rendered_body = text.strip()
    db.commit()
    return post


def publish(db: Session, job_id: int) -> JobPost:
    post = _post_for(db, job_id)
    if post.status != PostStatus.DRAFT:
        raise ConflictError("Only a draft post can be published.")
    if not post.form_url:
        raise InvalidInputError("The post has no application form yet. Prepare it again.")
    job = get_job(db, job_id)
    token, account = integrations.access_token(db, IntegrationProvider.LINKEDIN)

    commentary = compose_commentary(post.rendered_body or "", post.hashtags or [])
    urn = linkedin.create_post(
        token,
        account.account_id,
        commentary,
        link_url=post.form_url,
        link_title=f"Apply: {job.title}",
        link_description=" · ".join(p for p in (job.location, job.employment_type) if p),
    )
    post.external_id = urn
    post.external_url = linkedin.post_url(urn)
    post.status = PostStatus.POSTED
    post.posted_at = datetime.now(UTC)
    post.failure_reason = None
    job.status = JobStatus.OPEN
    db.commit()
    log.info("job_post_published", job_id=job.id, post_id=post.id)
    return post


def close(db: Session, job_id: int) -> JobPost:
    post = _post_for(db, job_id)
    if post.status == PostStatus.CLOSED:
        return post
    if post.form_id and post.status == PostStatus.POSTED:
        token, _ = integrations.access_token(db, IntegrationProvider.GOOGLE)
        google_forms.set_accepting_responses(token, post.form_id, False)
    post.status = PostStatus.CLOSED
    post.closed_at = datetime.now(UTC)
    job = get_job(db, job_id)
    if job.status == JobStatus.OPEN:
        job.status = JobStatus.CLOSED
    db.commit()
    log.info("job_post_closed", job_id=job_id, post_id=post.id)
    return post


def compose_commentary(text: str, hashtags: list[str]) -> str:
    # the editable text is plain; linkedin needs its reserved characters escaped
    body = linkedin.escape(text.strip())
    tags = " ".join(t for t in (linkedin.hashtag(h) for h in hashtags) if t)
    return f"{body}\n\n{tags}".strip() if tags else body


def _form_description(job: Job) -> str:
    parts = [plain_text(job.description_md or "")]
    parts.append(
        "Please sign in with a Google account so you can upload your CV (PDF or Word). "
        "Your details are used only to assess your application for this job."
    )
    return "\n\n".join(p for p in parts if p)


def plain_text(markdown: str) -> str:
    # forms descriptions are plain text, so the markdown marks would show as typed
    lines = markdown.strip().splitlines()
    # the form title already names the job
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    out = []
    for line in lines:
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        out.append(line.rstrip())
    text = "\n".join(out).strip()
    return re.sub(r"\n{3,}", "\n\n", text)
