from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class LlmCall(Base):
    # one row per llm answer: token counts only, never the prompt or the reply

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
    # e.g. groq:openai/gpt-oss-120b or gemini-3.5-flash
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    # the graph that made the call; empty for calls outside an agent run (co-pilot chat)
    source: Mapped[str | None] = mapped_column(String(60), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
