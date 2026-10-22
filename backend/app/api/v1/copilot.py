from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db, get_session_factory
from app.deps import require_interviewer
from app.models.user import User
from app.services import ai
from app.services import copilot as service
from app.services import copilot_chat as chat
from app.worker import queue

router = APIRouter(tags=["interview co-pilot"])


class SuggestIn(BaseModel):
    focus: str | None = Field(
        default=None,
        max_length=service.MAX_FOCUS_CHARS,
        description="optional topic to steer the questions, e.g. 'system design'",
    )


class BackgroundJobStarted(BaseModel):
    background_job_id: str


class SuggestionRequirement(BaseModel):
    id: int
    text: str
    must_have: bool


class Suggestion(BaseModel):
    id: int
    batch: int
    created_at: datetime
    requirement: SuggestionRequirement | None
    question: str
    why: str | None
    basis: dict[str, Any]
    evidence: list[dict[str, Any]]
    answer_points: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    focus: str | None
    warnings: list[str]
    was_used: bool
    model_name: str | None


class UsedIn(BaseModel):
    was_used: bool


class UsedOut(BaseModel):
    id: int
    was_used: bool


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=chat.MAX_MESSAGE_CHARS)


class ChatMessage(BaseModel):
    id: int
    role: str
    content: str
    status: str
    sources: list[dict[str, Any]]
    created_at: datetime


def chat_streamer() -> chat.Streamer:
    # tests swap this for a fake, gemini has no generic one
    return ai.stream_text


@router.get(
    "/interviews/{interview_id}/copilot/chat",
    response_model=list[ChatMessage],
    summary="The interviewer's chat with the co-pilot for this interview",
)
def list_chat(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[ChatMessage]:
    return [ChatMessage(**m) for m in chat.messages(db, interview_id)]


@router.post(
    "/interviews/{interview_id}/copilot/chat",
    summary="Ask the co-pilot; the reply streams as server-sent events",
    response_description="events: note, delta {text}, done {message}, error {message}",
)
def send_chat(
    interview_id: int,
    payload: ChatIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> StreamingResponse:
    prepared = chat.prepare(db, interview_id, payload.message)
    return StreamingResponse(
        chat.stream(prepared, chat_streamer(), get_session_factory()),
        media_type="text/event-stream",
        # no proxy may hold the reply back until it is complete
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/interviews/{interview_id}/copilot/suggestions",
    response_model=BackgroundJobStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask the co-pilot what to ask next (background job)",
)
def request_suggestions(
    interview_id: int,
    payload: SuggestIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> BackgroundJobStarted:
    service.check_can_suggest(db, interview_id)
    background_job_id = queue.enqueue(
        "app.worker.tasks.suggest_interview_questions_task",
        "copilot_suggestions",
        interview_id=interview_id,
        focus=service.clean_focus(payload.focus),
    )
    return BackgroundJobStarted(background_job_id=background_job_id)


@router.get(
    "/interviews/{interview_id}/copilot/suggestions",
    response_model=list[Suggestion],
    summary="Suggested questions for this interview, newest batch first",
)
def list_suggestions(
    interview_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> list[Suggestion]:
    return [Suggestion(**s) for s in service.suggestions(db, interview_id)]


@router.patch(
    "/interviews/{interview_id}/copilot/suggestions/{suggestion_id}",
    response_model=UsedOut,
    summary="Mark a suggested question as asked, or not",
)
def mark_used(
    interview_id: int,
    suggestion_id: int,
    payload: UsedIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_interviewer),
) -> UsedOut:
    row = service.mark_used(db, interview_id, suggestion_id, payload.was_used)
    return UsedOut(id=row.id, was_used=row.was_used)
