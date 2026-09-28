"""Чек-листы измерений и модель в AI-оценке (замечание 25.09.2026).

Один и тот же тендер получал у Claude и YandexGPT разные проценты: «Задачу» и «Компетенции»
модель оценивала числом по шкале с тремя опорными точками (0 / 50 / 100) и прыгала между
ними от запуска к запуску. Теперь модель проставляет статусы пунктам чек-листа, число
считает код — пункты хранятся рядом с оценкой (`task_checklist`, `competencies_checklist`).

`ai_provider` / `ai_model` — какая модель посчитала оценку: без них расхождение двух
версий нельзя отнести ни к смене модели, ни к её разбросу. У прежних строк остаются NULL.

Revision ID: 0054_ai_score_checklists
Revises: 0053_narrow_profile_okpd2
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_ai_score_checklists"
down_revision: Union[str, None] = "0053_narrow_profile_okpd2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_profile_scores",
        sa.Column("task_checklist", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "ai_profile_scores",
        sa.Column(
            "competencies_checklist", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column("ai_profile_scores", sa.Column("ai_provider", sa.String(20), nullable=True))
    op.add_column("ai_profile_scores", sa.Column("ai_model", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_profile_scores", "ai_model")
    op.drop_column("ai_profile_scores", "ai_provider")
    op.drop_column("ai_profile_scores", "competencies_checklist")
    op.drop_column("ai_profile_scores", "task_checklist")
