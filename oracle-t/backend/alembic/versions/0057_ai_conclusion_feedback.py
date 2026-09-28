"""Заключение ИИ и ответы специалистов (28.09.2026).

* `ai_profile_scores.conclusion` — заключение целиком: подходим ли мы и каким прибором, кто
  проходит вместо нас, стратегия входа, риски и метрики;
* `ai_score_feedback` — согласие или несогласие тендерного специалиста с заключением,
  с его текстом и снимками заключения до и после пересмотра.

Revision ID: 0057_ai_conclusion_feedback
Revises: 0056_deepseek_model
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_ai_conclusion_feedback"
down_revision: Union[str, None] = "0056_deepseek_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_profile_scores",
        sa.Column("conclusion", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "ai_score_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "score_before_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_profile_scores.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "score_after_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_profile_scores.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ai_response", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_score_feedback_tender_id", "ai_score_feedback", ["tender_id"])
    op.create_index("ix_ai_score_feedback_status", "ai_score_feedback", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ai_score_feedback_status", table_name="ai_score_feedback")
    op.drop_index("ix_ai_score_feedback_tender_id", table_name="ai_score_feedback")
    op.drop_table("ai_score_feedback")
    op.drop_column("ai_profile_scores", "conclusion")
