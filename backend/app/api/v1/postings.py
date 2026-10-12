from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.schemas.posting import (
    ApplicationOut,
    JobPostOut,
    PostStarted,
    PostTextIn,
    SyncStarted,
)
from app.services import applications as application_service
from app.services import job_posting
from app.services.jobs import get_job
from app.worker import queue

router = APIRouter(tags=["job posting and applications"])


@router.get(
    "/jobs/{job_id}/post",
    response_model=JobPostOut | None,
    summary="The job's LinkedIn post and application form, if any",
)
def get_post(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobPostOut | None:
    get_job(db, job_id)
    post = job_posting.current_post(db, job_id)
    return JobPostOut.model_validate(post) if post else None


@router.post(
    "/jobs/{job_id}/post",
    response_model=PostStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Copy the application form and draft the LinkedIn post (background job)",
)
def prepare_post(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> PostStarted:
    post = job_posting.start_post(db, job_id, user.id)
    background_job_id = queue.enqueue(
        "app.worker.tasks.prepare_job_post_task", "job_post", post_id=post.id
    )
    return PostStarted(post=JobPostOut.model_validate(post), background_job_id=background_job_id)


@router.put("/jobs/{job_id}/post/text", response_model=JobPostOut, summary="Edit the draft post")
def update_post_text(
    job_id: int,
    payload: PostTextIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobPostOut:
    return JobPostOut.model_validate(job_posting.update_text(db, job_id, payload.text))


@router.post(
    "/jobs/{job_id}/post/publish", response_model=JobPostOut, summary="Publish the post on LinkedIn"
)
def publish_post(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobPostOut:
    return JobPostOut.model_validate(job_posting.publish(db, job_id))


@router.post(
    "/jobs/{job_id}/post/close",
    response_model=JobPostOut,
    summary="Stop accepting applications",
)
def close_post(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobPostOut:
    return JobPostOut.model_validate(job_posting.close(db, job_id))


@router.post(
    "/jobs/{job_id}/applications/sync",
    response_model=SyncStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Import new form responses and their CVs (background job)",
)
def sync_applications(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> SyncStarted:
    get_job(db, job_id)
    background_job_id = queue.enqueue(
        "app.worker.tasks.sync_applications_task", "application_sync", job_id=job_id
    )
    return SyncStarted(background_job_id=background_job_id)


@router.get(
    "/jobs/{job_id}/applications",
    response_model=list[ApplicationOut],
    summary="Applications collected for a job",
)
def list_applications(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[ApplicationOut]:
    return [ApplicationOut(**a) for a in application_service.list_applications(db, job_id)]


@router.get("/applications/{application_id}/cv", summary="Download an applicant's CV")
def download_cv(
    application_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> FileResponse:
    cv = application_service.cv_file(db, application_id)
    return FileResponse(cv.storage_path, media_type=cv.content_type, filename=cv.original_filename)
