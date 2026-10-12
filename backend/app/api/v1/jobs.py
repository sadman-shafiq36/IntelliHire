from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.job import JobDescriptionMessage
from app.models.user import User
from app.schemas.job import (
    ChatMessageIn,
    ChatMessageOut,
    ChatResponse,
    DescriptionIn,
    GenerateResponse,
    JobIn,
    JobListItem,
    JobOut,
    VersionOut,
)
from app.services import description_versions
from app.services import job_descriptions as jd_service
from app.services import jobs as job_service
from app.worker import queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobListItem], summary="List jobs")
def list_jobs(
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[JobListItem]:
    return job_service.list_jobs(db)


@router.post(
    "", response_model=JobOut, status_code=status.HTTP_201_CREATED, summary="Create a job profile"
)
def create_job(
    payload: JobIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> JobOut:
    return JobOut.model_validate(job_service.create_job(db, payload, created_by_id=user.id))


@router.get("/{job_id}", response_model=JobOut, summary="A job with its requirements")
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobOut:
    return JobOut.model_validate(job_service.get_job(db, job_id))


@router.put("/{job_id}", response_model=JobOut, summary="Update a job profile")
def update_job(
    job_id: int,
    payload: JobIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> JobOut:
    return JobOut.model_validate(job_service.update_job(db, job_id, payload))


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a job")
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> None:
    job_service.delete_job(db, job_id)


@router.put(
    "/{job_id}/description",
    response_model=JobOut,
    summary="Save the reviewed job description",
)
def save_description(
    job_id: int,
    payload: DescriptionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> JobOut:
    job = job_service.save_description(db, job_id, payload.description_md, user_id=user.id)
    return JobOut.model_validate(job)


@router.post(
    "/{job_id}/description/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Draft the job description with the JD agent (background job)",
)
def generate_description(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> GenerateResponse:
    job = job_service.get_job(db, job_id)
    background_job_id = queue.enqueue(
        "app.worker.tasks.generate_job_description_task", "job_description", job_id=job.id
    )
    return GenerateResponse(background_job_id=background_job_id)


@router.get(
    "/{job_id}/description/versions",
    response_model=list[VersionOut],
    summary="Every version of the description, newest first",
)
def list_versions(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[VersionOut]:
    job_service.get_job(db, job_id)
    return [VersionOut.model_validate(v) for v in description_versions.list_versions(db, job_id)]


@router.post(
    "/{job_id}/description/versions/{version_id}/restore",
    response_model=JobOut,
    summary="Make an earlier version current again (undo)",
)
def restore_version(
    job_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> JobOut:
    job = job_service.get_job(db, job_id)
    description_versions.restore_version(db, job, version_id, created_by_id=user.id)
    return JobOut.model_validate(job_service.get_job(db, job_id))


@router.get(
    "/{job_id}/description/chat",
    response_model=list[ChatMessageOut],
    summary="The conversation with the job description agent",
)
def list_chat(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[ChatMessageOut]:
    return [_message_out(m) for m in jd_service.list_messages(db, job_id)]


@router.post(
    "/{job_id}/description/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask the agent to change the description (background job)",
)
def send_chat(
    job_id: int,
    payload: ChatMessageIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> ChatResponse:
    message = jd_service.add_user_message(db, job_id, payload.message, user_id=user.id)
    background_job_id = queue.enqueue(
        "app.worker.tasks.revise_job_description_task",
        "job_description_chat",
        job_id=job_id,
        message_id=message.id,
    )
    db.refresh(message)
    return ChatResponse(background_job_id=background_job_id, message=_message_out(message))


def _message_out(message: JobDescriptionMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        status=message.status,
        version=message.version.version if message.version_id and message.version else None,
        warnings=message.warnings or [],
        created_at=message.created_at,
    )
