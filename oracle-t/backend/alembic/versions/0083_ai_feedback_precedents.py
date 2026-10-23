"""Замечания к заключению ИИ — прецеденты для похожих закупок (23.10.2026, раздел 71
ARCHITECTURE.md).

`ai_score_feedback.share_as_precedent` — переносить ли несогласие специалиста в разбор
похожих закупок. По умолчанию переносится: в замечаниях обычно знание о компании и приборах
(«у нас есть допуск СРО», «МИРТЕК-32 это поддерживает»), которое верно не только для одной
закупки. Замечание о частностях одной закупки автор или администратор выключает.

Revision ID: 0083_ai_feedback_precedents
Revises: 0082_compliance_reviews
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083_ai_feedback_precedents"
down_revision: Union[str, None] = "0082_compliance_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_score_feedback",
        sa.Column("share_as_precedent", sa.Boolean(), server_default="true", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ai_score_feedback", "share_as_precedent")
