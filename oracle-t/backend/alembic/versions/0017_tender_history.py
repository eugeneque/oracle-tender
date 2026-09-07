"""история изменений тендера (раздел 5.6 ТЗ — Этап 7)

Одна таблица под правки классификации, отметки релевантности и комментарии пользователей.
Индекс по (tender_id, created_at) — история всегда читается лентой по одному тендеру в
обратном хронологическом порядке.

Revision ID: 0017_tender_history
Revises: 0016_tender_analysis
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_tender_history"
down_revision: Union[str, None] = "0016_tender_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Без каскада на пользователя: удаление учётной записи не должно стирать
        # обоснование решений по тендеру.
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="field_change"),
        sa.Column("field_name", sa.String(length=60), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tender_history_tender_created",
        "tender_history",
        ["tender_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tender_history_tender_created", table_name="tender_history")
    op.drop_table("tender_history")
