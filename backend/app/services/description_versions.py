# every change to a job description goes through record_version, so the history
# is complete and undo is just restoring an older version

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.logging_config import get_logger
from app.models.enums import DescriptionSource
from app.models.job import Job, JobDescriptionVersion

log = get_logger(__name__)


def record_version(
    db: Session,
    job: Job,
    description_md: str,
    citations: list[dict[str, Any]] | None,
    source: DescriptionSource,
    created_by_id: int | None = None,
    restored_from: int | None = None,
) -> JobDescriptionVersion:
    latest = db.scalar(
        select(func.max(JobDescriptionVersion.version)).where(
            JobDescriptionVersion.job_id == job.id
        )
    )
    version = JobDescriptionVersion(
        job_id=job.id,
        version=(latest or 0) + 1,
        description_md=description_md,
        policy_citations=citations,
        source=source,
        created_by_id=created_by_id,
        restored_from=restored_from,
    )
    db.add(version)
    job.description_md = description_md
    job.policy_citations = citations
    db.flush()
    log.info("jd_version_recorded", job_id=job.id, version=version.version, source=source.value)
    return version


def list_versions(db: Session, job_id: int) -> list[JobDescriptionVersion]:
    return list(
        db.scalars(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id == job_id)
            .order_by(JobDescriptionVersion.version.desc())
        )
    )


def restore_version(
    db: Session, job: Job, version_id: int, created_by_id: int | None
) -> JobDescriptionVersion:
    old = db.get(JobDescriptionVersion, version_id)
    if old is None or old.job_id != job.id:
        raise NotFoundError("That version no longer exists.")
    # restoring adds a new version on top, so the restore itself can be undone
    restored = record_version(
        db,
        job,
        old.description_md,
        old.policy_citations,
        DescriptionSource.RESTORED,
        created_by_id=created_by_id,
        restored_from=old.version,
    )
    db.commit()
    return restored


def current_version(db: Session, job_id: int) -> JobDescriptionVersion | None:
    return db.scalar(
        select(JobDescriptionVersion)
        .where(JobDescriptionVersion.job_id == job_id)
        .order_by(JobDescriptionVersion.version.desc())
        .limit(1)
    )
