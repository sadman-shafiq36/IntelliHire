from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import IntegrationProvider


class IntegrationAccount(Base, TimestampMixin):
    # one connected account per provider for the whole deployment. tokens are
    # encrypted at rest and never returned by the api or written to logs

    __tablename__ = "integration_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(IntegrationProvider, native_enum=False, length=20), unique=True, nullable=False
    )
    # linkedin member id or google email, used as the post author / form owner
    account_id: Mapped[str] = mapped_column(String(200), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(200))
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(String(1000))
    connected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
