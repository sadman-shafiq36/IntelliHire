# numbers and to-dos for the interviewer's overview page. only stages that exist
# in the product are counted

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.analysis import Assessment
from app.models.candidate import Application, Candidate, CvDocument
from app.models.enums import ExtractionStatus, PolicyStatus, PostStatus, ScreeningStatus
from app.models.job import CompanyPolicy, Job
from app.models.screening import Screening
from app.services import integrations
from app.services.interviews import counts_by_job as interview_counts
from app.services.scoring import counts_by_job

STALE_SYNC = timedelta(hours=24)
RECENT = 6


def overview(db: Session) -> dict[str, Any]:
    jobs = list(db.scalars(select(Job).options(selectinload(Job.posts)).order_by(Job.id.desc())))
    latest_post = {
        job.id: max(job.posts, key=lambda p: p.id) if job.posts else None for job in jobs
    }
    app_counts = dict(
        db.execute(select(Application.job_id, func.count()).group_by(Application.job_id)).all()
    )

    described = [j for j in jobs if j.description_md]
    live = [j for j in jobs if (p := latest_post[j.id]) and p.status == PostStatus.POSTED]
    week_ago = datetime.now(UTC) - timedelta(days=7)
    new_this_week = db.scalar(
        select(func.count()).select_from(Application).where(Application.created_at >= week_ago)
    )

    attention: list[dict[str, Any]] = []
    for item in integrations.status(db):
        if item["configured"] and not item["connected"]:
            attention.append(
                {
                    "kind": "integration",
                    "title": f"Connect {item['label']}",
                    "detail": "Needed to post jobs and collect applications."
                    if not item["expired"]
                    else f"The {item['label']} connection has expired.",
                    "job_id": None,
                }
            )
    for job in jobs:
        post = latest_post[job.id]
        if not job.description_md:
            attention.append(
                {
                    "kind": "description",
                    "title": f"Write the description for {job.title}",
                    "detail": "The agent can draft it from the requirements and your policies.",
                    "job_id": job.id,
                }
            )
        elif post is None:
            attention.append(
                {
                    "kind": "post",
                    "title": f"Post {job.title} on LinkedIn",
                    "detail": "The description is ready; prepare the post and its form.",
                    "job_id": job.id,
                }
            )
        elif post.status == PostStatus.DRAFT:
            attention.append(
                {
                    "kind": "publish",
                    "title": f"Publish the draft post for {job.title}",
                    "detail": "Review the text and the application form, then publish.",
                    "job_id": job.id,
                }
            )
        elif post.status == PostStatus.FAILED:
            attention.append(
                {
                    "kind": "failed",
                    "title": f"Posting {job.title} failed",
                    "detail": post.failure_reason or "Prepare the post again.",
                    "job_id": job.id,
                }
            )
        elif post.status == PostStatus.POSTED and (
            post.last_synced_at is None
            or _aware(post.last_synced_at) < datetime.now(UTC) - STALE_SYNC
        ):
            attention.append(
                {
                    "kind": "sync",
                    "title": f"Check {job.title} for new applications",
                    "detail": "Not synced in the last day."
                    if post.last_synced_at
                    else "Applications have not been synced yet.",
                    "job_id": job.id,
                }
            )
    pending_cvs = dict(
        db.execute(
            select(CvDocument.job_id, func.count())
            .where(CvDocument.status.in_(ExtractionStatus.waiting()))
            .group_by(CvDocument.job_id)
        ).all()
    )
    for job in jobs:
        waiting = pending_cvs.get(job.id, 0)
        if waiting:
            attention.insert(
                0,
                {
                    "kind": "screen",
                    "title": f"Screen {waiting} CV{_s(waiting)} for {job.title}",
                    "detail": "They are waiting to be matched against the requirements.",
                    "job_id": job.id,
                },
            )
    scoring = counts_by_job(db)
    titles = {job.id: job.title for job in jobs}
    for job_id, row in scoring.items():
        # screening first: no point scoring a job whose ranking is still filling in
        if row["waiting"] and not pending_cvs.get(job_id) and job_id in titles:
            attention.insert(
                0,
                {
                    "kind": "score",
                    "title": f"Score {row['waiting']} candidate{_s(row['waiting'])} for "
                    f"{titles[job_id]}",
                    "detail": "They are in the screening ranking without an up-to-date score.",
                    "job_id": job_id,
                },
            )
    booked = interview_counts(db)
    for job_id, row in booked.items():
        if row["to_schedule"] and job_id in titles:
            attention.insert(
                0,
                {
                    "kind": "interview",
                    "title": f"Schedule {row['to_schedule']} interview{_s(row['to_schedule'])} "
                    f"for {titles[job_id]}",
                    "detail": "Selected for interview, not booked yet.",
                    "job_id": job_id,
                },
            )
    failed_policies = db.scalar(
        select(func.count())
        .select_from(CompanyPolicy)
        .where(CompanyPolicy.status == PolicyStatus.FAILED)
    )
    if failed_policies:
        attention.append(
            {
                "kind": "policy",
                "title": f"{failed_policies} policy document{'s' if failed_policies != 1 else ''} "
                "could not be read",
                "detail": "Upload a text-based version so the agent can use it.",
                "job_id": None,
            }
        )

    recent = db.execute(
        select(Application, Candidate.full_name, Job.title)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Job, Job.id == Application.job_id)
        .order_by(Application.created_at.desc())
        .limit(RECENT)
    ).all()

    return {
        "pipeline": {
            "roles": len(jobs),
            "described": len(described),
            "live": len(live),
            "applications": sum(app_counts.values()),
            "new_this_week": new_this_week or 0,
            "screened": db.scalar(
                select(func.count())
                .select_from(Screening)
                .where(Screening.status.in_([ScreeningStatus.SCREENED, ScreeningStatus.EXCLUDED]))
            )
            or 0,
            "scored": sum(r["scored"] for r in scoring.values()),
            "shortlisted": sum(r["shortlisted"] for r in scoring.values()),
            "selected": sum(r["selected"] for r in scoring.values()),
            "interviews_upcoming": sum(r["upcoming"] for r in booked.values()),
            "interviewed": sum(r["completed"] for r in booked.values()),
            "interviews_live": sum(r["live"] for r in booked.values()),
            "assessed": db.scalar(
                select(func.count(func.distinct(Assessment.candidate_id))).where(
                    Assessment.status == "assessed"
                )
            )
            or 0,
            "shortlist_ready": db.scalar(
                select(func.count())
                .select_from(Screening)
                .where(Screening.status == ScreeningStatus.SCREENED)
            )
            or 0,
        },
        "policies_indexed": db.scalar(
            select(func.count())
            .select_from(CompanyPolicy)
            .where(CompanyPolicy.status == PolicyStatus.INDEXED)
        )
        or 0,
        "attention": attention[:8],
        "recent_applications": [
            {
                "id": app.id,
                "job_id": app.job_id,
                "job_title": title,
                "full_name": name,
                "submitted_at": app.submitted_at or app.created_at,
            }
            for app, name, title in recent
        ],
        "live_jobs": [
            {
                "id": job.id,
                "title": job.title,
                "location": job.location,
                "applications": app_counts.get(job.id, 0),
                "post_url": latest_post[job.id].external_url,
            }
            for job in live
        ],
    }


def _s(count: int) -> str:
    return "" if count == 1 else "s"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
