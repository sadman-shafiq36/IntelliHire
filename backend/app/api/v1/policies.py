from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.schemas.policy import PolicyOut, PolicySearchHit, PolicyUploadResponse
from app.services import policies as policy_service
from app.tools.policy_search import search_company_policy
from app.worker import queue

router = APIRouter(prefix="/policies", tags=["company policies"])


@router.post(
    "",
    response_model=PolicyUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a company policy and queue it for indexing",
)
async def upload_policy(
    file: UploadFile = File(...),
    title: str = Form("", max_length=300),
    db: Session = Depends(get_db),
    user: User = Depends(require_interviewer),
) -> PolicyUploadResponse:
    data = await file.read()
    policy = policy_service.save_policy_upload(
        db,
        title=title,
        filename=file.filename or "policy",
        content_type=file.content_type or "",
        data=data,
        uploaded_by_id=user.id,
    )
    job_id = queue.enqueue(
        "app.worker.tasks.index_policy_task", "policy_indexing", policy_id=policy.id
    )
    return PolicyUploadResponse(policy=PolicyOut.model_validate(policy), background_job_id=job_id)


@router.get("", response_model=list[PolicyOut], summary="List company policies")
def list_policies(
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[PolicyOut]:
    return [PolicyOut.model_validate(p) for p in policy_service.list_policies(db)]


@router.get(
    "/search",
    response_model=list[PolicySearchHit],
    summary="Search indexed policy passages, the same retrieval the agents use",
)
def search_policies(
    q: str = Query(min_length=2, max_length=300),
    k: int = Query(4, ge=1, le=10),
    _user: User = Depends(require_interviewer),
) -> list[PolicySearchHit]:
    return [PolicySearchHit(**hit.__dict__) for hit in search_company_policy(q, k=k)]


@router.post(
    "/{policy_id}/reindex",
    response_model=PolicyUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Index a policy again, e.g. after a failure",
)
def reindex_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> PolicyUploadResponse:
    policy = policy_service.get_policy(db, policy_id)
    job_id = queue.enqueue(
        "app.worker.tasks.index_policy_task", "policy_indexing", policy_id=policy.id
    )
    return PolicyUploadResponse(policy=PolicyOut.model_validate(policy), background_job_id=job_id)


@router.delete(
    "/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a company policy"
)
def delete_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> None:
    policy_service.delete_policy(db, policy_id)
