"""Избранные закупки (замечание тестировщика 16.09.2026).

Личный список отложенных закупок у каждого пользователя: пара «пользователь — тендер»
уникальна, заметка необязательна. См. `app/models/tender_bookmark.py`.

Revision ID: 0043_tender_bookmarks
Revises: 0042_upper_software
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_tender_bookmarks"
down_revision: Union[str, None] = "0042_upper_software"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "tender_id", name="uq_tender_bookmarks_user_tender"),
    )
    op.create_index("ix_tender_bookmarks_user_id", "tender_bookmarks", ["user_id"])
    op.create_index("ix_tender_bookmarks_tender_id", "tender_bookmarks", ["tender_id"])


def downgrade() -> None:
    op.drop_table("tender_bookmarks")
