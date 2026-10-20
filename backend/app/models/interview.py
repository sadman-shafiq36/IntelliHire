from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import InterviewStatus, SpeakerRole


class Interview(Base, TimestampMixin):
    # booked by the scheduling agent through the mcp server: a google calendar event when
    # google is connected, otherwise an emailed .ics only. the call itself is held in
    # intellihire's own room, never google meet

    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    interviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone_name: Mapped[str] = mapped_column(String(60), nullable=False, default="UTC")

    duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=45, server_default="45"
    )
    # sha-256 of the candidate's link token. the token itself is only ever in their email,
    # so a copy of the database can't be used to join. a reschedule issues a new one
    join_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    join_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    calendar_event_id: Mapped[str | None] = mapped_column(String(200))
    # gmail or smtp: how the last email went out
    invitation_channel: Mapped[str | None] = mapped_column(String(20))
    invitation_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # one uid for the life of the interview, sequence goes up with every change
    ics_uid: Mapped[str | None] = mapped_column(String(200))
    ics_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # the background job working on it, so two can't run at once
    pending_action: Mapped[str | None] = mapped_column(String(20))
    pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # why the last attempt didn't go through, shown to the interviewer
    last_outcome: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus, native_enum=False, length=20),
        nullable=False,
        default=InterviewStatus.SCHEDULED,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))

    participants: Mapped[list["InterviewParticipant"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
        order_by="TranscriptTurn.sequence",
    )


class InterviewParticipant(Base, TimestampMixin):
    # separate rows so reconnects after a dropped connection are kept

    __tablename__ = "interview_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, nullable=False
    )

    role: Mapped[SpeakerRole] = mapped_column(
        Enum(SpeakerRole, native_enum=False, length=20), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    display_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # who they are in the video room, so a "left" event closes the right row
    identity: Mapped[str | None] = mapped_column(String(120), index=True)

    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    interview: Mapped["Interview"] = relationship(back_populates="participants")


class TranscriptTurn(Base, TimestampMixin):
    # speaker comes from whose audio track it was heard on, not diarisation.
    # start_offset_ms is for citing evidence, spoken_at is for display

    __tablename__ = "transcript_turns"
    __table_args__ = (Index("ix_turn_interview_sequence", "interview_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False
    )

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_role: Mapped[SpeakerRole] = mapped_column(
        Enum(SpeakerRole, native_enum=False, length=20), nullable=False
    )
    speaker_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spoken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # analysis discounts turns the speech service couldn't transcribe cleanly
    is_uncertain: Mapped[bool] = mapped_column(default=False, nullable=False)
    # groq, whisper (the local fallback), or none when neither could transcribe it
    stt_provider: Mapped[str | None] = mapped_column(String(20))

    interview: Mapped["Interview"] = relationship(back_populates="turns")
