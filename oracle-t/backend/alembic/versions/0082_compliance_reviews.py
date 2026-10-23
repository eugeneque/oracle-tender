"""Оценка специалистом вердиктов матрицы соответствия (23.10.2026, раздел 70 ARCHITECTURE.md).

`compliance_verdict_reviews` — согласие или исправление вердикта по ячейке «требование ×
производитель». Исправление перекрывает вердикт модели в своей закупке и уходит модели
прецедентом при разборе похожих требований других закупок.

Revision ID: 0082_compliance_reviews
Revises: 0081_card_deadlines
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_compliance_reviews"
down_revision: Union[str, None] = "0081_card_deadlines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compliance_verdict_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("corrected_status", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.String(length=20), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("requirement_key", sa.Text(), nullable=False),
        sa.Column("model_status", sa.String(length=20), nullable=True),
        sa.Column("model_explanation", sa.Text(), nullable=True),
        sa.Column("model_source", sa.String(length=30), nullable=True),
        sa.Column("model_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_compliance_verdict_reviews_tender_id", "compliance_verdict_reviews", ["tender_id"]
    )
    op.create_index(
        "ix_compliance_verdict_reviews_manufacturer_id",
        "compliance_verdict_reviews",
        ["manufacturer_id"],
    )
    op.create_index(
        "ix_compliance_verdict_reviews_is_current", "compliance_verdict_reviews", ["is_current"]
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_verdict_reviews_is_current", table_name="compliance_verdict_reviews")
    op.drop_index(
        "ix_compliance_verdict_reviews_manufacturer_id", table_name="compliance_verdict_reviews"
    )
    op.drop_index("ix_compliance_verdict_reviews_tender_id", table_name="compliance_verdict_reviews")
    op.drop_table("compliance_verdict_reviews")
