"""job description versions and chat

Revision ID: 75bf878c2025
Revises: 4a10f5c757f5
Create Date: 2026-10-12 04:36:49.931558
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "75bf878c2025"
down_revision: str | None = "4a10f5c757f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_description_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description_md", sa.Text(), nullable=False),
        sa.Column("policy_citations", sa.JSON(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "GENERATED",
                "CHAT",
                "MANUAL",
                "RESTORED",
                name="descriptionsource",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "version"),
    )
    op.create_index(
        op.f("ix_job_description_versions_job_id"),
        "job_description_versions",
        ["job_id"],
        unique=False,
    )
    op.create_table(
        "job_description_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("USER", "ASSISTANT", name="chatrole", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "DONE", "FAILED", name="chatmessagestatus", native_enum=False, length=20
            ),
            nullable=False,
        ),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["version_id"], ["job_description_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_job_description_messages_job_id"),
        "job_description_messages",
        ["job_id"],
        unique=False,
    )

    # descriptions written before versioning become version 1
    op.execute(
        """
        INSERT INTO job_description_versions (job_id, version, description_md, policy_citations,
                                              source)
        SELECT id, 1, description_md, policy_citations, 'GENERATED'
        FROM jobs
        WHERE description_md IS NOT NULL AND description_md <> ''
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_job_description_messages_job_id"), table_name="job_description_messages"
    )
    op.drop_table("job_description_messages")
    op.drop_index(
        op.f("ix_job_description_versions_job_id"), table_name="job_description_versions"
    )
    op.drop_table("job_description_versions")
