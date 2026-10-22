# company policies and interview reference material: the same upload, indexing and
# search, each kind behind its own path and in its own chroma collection

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.enums import DocumentKind
from app.models.user import User
from app.rag.policy_store import store_for
from app.schemas.policy import PolicyOut, PolicySearchHit, PolicyUploadResponse
from app.services import policies as policy_service
from app.tools.policy_search import search_company_policy
from app.worker import queue


def _make_router(kind: DocumentKind, prefix: str, noun: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.post(
        "",
        response_model=PolicyUploadResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary=f"Upload a {noun} and queue it for indexing",
    )
    async def upload(
        file: UploadFile = File(...),
        title: str = Form("", max_length=300),
        db: Session = Depends(get_db),
        user: User = Depends(require_interviewer),
    ) -> PolicyUploadResponse:
        data = await file.read()
        policy = policy_service.save_policy_upload(
            db,
            title=title,
            filename=file.filename or kind.value,
            content_type=file.content_type or "",
            data=data,
            uploaded_by_id=user.id,
            kind=kind,
        )
        job_id = queue.enqueue(
            "app.worker.tasks.index_policy_task", "policy_indexing", policy_id=policy.id
        )
        return PolicyUploadResponse(
            policy=PolicyOut.model_validate(policy), background_job_id=job_id
        )

    @router.get("", response_model=list[PolicyOut], summary=f"List {tag}")
    def list_all(
        db: Session = Depends(get_db),
        _user: User = Depends(require_interviewer),
    ) -> list[PolicyOut]:
        return [PolicyOut.model_validate(p) for p in policy_service.list_policies(db, kind)]

    @router.get(
        "/search",
        response_model=list[PolicySearchHit],
        summary="Search indexed passages, the same retrieval the agents use",
    )
    def search(
        q: str = Query(min_length=2, max_length=300),
        k: int = Query(4, ge=1, le=10),
        _user: User = Depends(require_interviewer),
    ) -> list[PolicySearchHit]:
        hits = search_company_policy(q, k=k, store=store_for(kind))
        return [PolicySearchHit(**hit.__dict__) for hit in hits]

    @router.post(
        "/{policy_id}/reindex",
        response_model=PolicyUploadResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary=f"Index a {noun} again, e.g. after a failure",
    )
    def reindex(
        policy_id: int,
        db: Session = Depends(get_db),
        _user: User = Depends(require_interviewer),
    ) -> PolicyUploadResponse:
        policy = policy_service.get_policy(db, policy_id, kind)
        job_id = queue.enqueue(
            "app.worker.tasks.index_policy_task", "policy_indexing", policy_id=policy.id
        )
        return PolicyUploadResponse(
            policy=PolicyOut.model_validate(policy), background_job_id=job_id
        )

    @router.delete(
        "/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, summary=f"Delete a {noun}"
    )
    def delete(
        policy_id: int,
        db: Session = Depends(get_db),
        _user: User = Depends(require_interviewer),
    ) -> None:
        policy_service.delete_policy(db, policy_id, kind)

    return router


router = _make_router(DocumentKind.POLICY, "/policies", "company policy", "company policies")
references_router = _make_router(
    DocumentKind.REFERENCE, "/references", "reference document", "interview references"
)
