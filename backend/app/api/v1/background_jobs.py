# "background jobs", not "jobs" - a Job is a role being hired for

from typing import Any

from fastapi import APIRouter, Depends

from app.deps import require_interviewer
from app.errors import NotFoundError
from app.models.user import User
from app.worker.queue import read_progress

router = APIRouter(prefix="/background-jobs", tags=["background jobs"])


@router.get("/{job_id}", summary="Progress of a queued or running job")
def get_job_progress(
    job_id: str,
    _user: User = Depends(require_interviewer),
) -> dict[str, Any]:
    # interviewer only, the payload refs would expose screening activity
    progress = read_progress(job_id)
    if progress is None:
        raise NotFoundError("That job is not known. It may have finished more than a day ago.")
    return progress
