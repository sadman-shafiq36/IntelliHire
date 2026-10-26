from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CandidateFeedback(Base, TimestampMixin):
    # feedback to one candidate for one job at one stage: screening (only after a person marked
    # them not selected for interview) or interview. the letter is a checked draft until a person
    # approves it; nothing is sent before that. events hold actions, user ids and times only

    __tablename__ = "candidate_feedback"
    __table_args__ = (
        UniqueConstraint("candidate_id", "job_id", "stage", name="uq_feedback_candidate_job_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    interview_id: Mapped[int | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )

    # screening | interview
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    # not_progressing | on_hold | next_round (screening is always not_progressing)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    # new | drafting | in_review | approved | discarded | sent | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    message: Mapped[str | None] = mapped_column(String(500))
    thread_id: Mapped[str | None] = mapped_column(String(100))

    # interviewer and recruiter notes as given, and the ones screened out with the reason
    notes: Mapped[list | None] = mapped_column(JSON)
    excluded_notes: Mapped[list | None] = mapped_column(JSON)
    # each point names its requirement and the stored row it rests on
    points: Mapped[list | None] = mapped_column(JSON)
    # subject, body (with {candidate_name} still in it) and the sentences with their points
    draft: Mapped[dict | None] = mapped_column(JSON)
    # an edit waiting to be checked
    pending_edit: Mapped[dict | None] = mapped_column(JSON)
    guardrail: Mapped[dict | None] = mapped_column(JSON)
    # low_confidence | unreadable | no_evidence | ai_unavailable | guardrail
    fallback_reason: Mapped[str | None] = mapped_column(String(40))
    warnings: Mapped[list | None] = mapped_column(JSON)
    events: Mapped[list | None] = mapped_column(JSON)
    model_name: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(40))

    marked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # gmail | smtp | outbox
    send_channel: Mapped[str | None] = mapped_column(String(20))
    message_id: Mapped[str | None] = mapped_column(String(300))
    # the .eml written when the email service couldn't be reached; deleted with the candidate
    outbox_path: Mapped[str | None] = mapped_column(String(500))
