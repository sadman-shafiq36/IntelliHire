from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import require_interviewer
from app.models.user import User
from app.services import candidates as service

router = APIRouter(tags=["candidates"])


@router.delete(
    "/candidates/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a candidate and all their data: CVs, screening, scores and AI working state",
)
def delete_candidate(
    candidate_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> Response:
    service.delete_candidate(db, candidate_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
