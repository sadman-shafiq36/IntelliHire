from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.errors import InvalidInputError, NotFoundError
from app.logging_config import get_logger
from app.models.enums import DescriptionSource
from app.models.job import Job, JobRequirement
from app.schemas.job import JobIn, JobListItem, RequirementIn

log = get_logger(__name__)


def get_job(db: Session, job_id: int) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id).options(selectinload(Job.requirements)))
    if job is None:
        raise NotFoundError("That job no longer exists.")
    return job


def list_jobs(db: Session) -> list[JobListItem]:
    from sqlalchemy import func

    from app.models.candidate import Application

    jobs = db.scalars(
        select(Job)
        .options(selectinload(Job.requirements), selectinload(Job.posts))
        .order_by(Job.updated_at.desc())
    )
    from app.models.candidate import CvDocument
    from app.models.enums import ExtractionStatus, ScreeningStatus
    from app.models.screening import Screening

    counts = dict(
        db.execute(select(Application.job_id, func.count()).group_by(Application.job_id)).all()
    )
    pending = dict(
        db.execute(
            select(CvDocument.job_id, func.count())
            .where(CvDocument.status.in_(ExtractionStatus.waiting()))
            .group_by(CvDocument.job_id)
        ).all()
    )
    screened = dict(
        db.execute(
            select(Screening.job_id, func.count())
            .where(Screening.status.in_([ScreeningStatus.SCREENED, ScreeningStatus.EXCLUDED]))
            .group_by(Screening.job_id)
        ).all()
    )
    from app.services.scoring import counts_by_job

    scoring = counts_by_job(db)
    return [
        JobListItem(
            id=job.id,
            title=job.title,
            department=job.department,
            location=job.location,
            status=job.status,
            requirement_count=len(job.requirements),
            mandatory_count=sum(1 for r in job.requirements if r.is_mandatory),
            has_description=bool(job.description_md),
            post_status=max(job.posts, key=lambda p: p.id).status.value if job.posts else None,
            application_count=counts.get(job.id, 0),
            pending_cv_count=pending.get(job.id, 0),
            screened_count=screened.get(job.id, 0),
            scored_count=scoring.get(job.id, {}).get("scored", 0),
            unscored_count=scoring.get(job.id, {}).get("waiting", 0),
            shortlisted_count=scoring.get(job.id, {}).get("shortlisted", 0),
            updated_at=job.updated_at,
        )
        for job in jobs
    ]


def create_job(db: Session, payload: JobIn, created_by_id: int | None) -> Job:
    job = Job(created_by_id=created_by_id)
    _apply_profile(job, payload)
    job.requirements = [_new_requirement(r) for r in payload.requirements]
    db.add(job)
    db.commit()
    log.info("job_created", job_id=job.id, requirement_count=len(job.requirements))
    return get_job(db, job.id)


def update_job(db: Session, job_id: int, payload: JobIn) -> Job:
    job = get_job(db, job_id)
    _apply_profile(job, payload)

    existing = {r.id: r for r in job.requirements}
    unknown = [r.id for r in payload.requirements if r.id is not None and r.id not in existing]
    if unknown:
        raise InvalidInputError("Some requirements don't belong to this job. Reload and retry.")

    kept: list[JobRequirement] = []
    for item in payload.requirements:
        if item.id is None:
            kept.append(_new_requirement(item))
            continue
        requirement = existing[item.id]
        requirement.text = item.text
        requirement.category = item.category
        requirement.is_mandatory = item.is_mandatory
        requirement.weight = item.weight
        kept.append(requirement)
    # delete-orphan removes the ones that were left out
    job.requirements = kept

    db.commit()
    log.info("job_updated", job_id=job.id, requirement_count=len(kept))
    return get_job(db, job.id)


def delete_job(db: Session, job_id: int) -> None:
    job = get_job(db, job_id)
    db.delete(job)
    db.commit()
    log.info("job_deleted", job_id=job_id)


def save_description(db: Session, job_id: int, description_md: str, user_id: int) -> Job:
    from app.services.description_versions import record_version

    job = get_job(db, job_id)
    text = description_md.strip()
    if text != (job.description_md or "").strip():
        # citations stay, the passages are still what the text was built from
        record_version(
            db, job, text, job.policy_citations, DescriptionSource.MANUAL, created_by_id=user_id
        )
        db.commit()
        log.info("job_description_saved", job_id=job.id)
    return get_job(db, job.id)


def _apply_profile(job: Job, payload: JobIn) -> None:
    job.title = payload.title
    job.department = payload.department
    job.location = payload.location
    job.employment_type = payload.employment_type
    job.min_years_experience = payload.min_years_experience
    job.summary = payload.summary


def _new_requirement(item: RequirementIn) -> JobRequirement:
    return JobRequirement(
        text=item.text,
        category=item.category,
        is_mandatory=item.is_mandatory,
        weight=item.weight,
    )
