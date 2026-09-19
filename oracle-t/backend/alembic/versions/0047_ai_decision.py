"""Решение ИИ «смотреть / не смотреть» по трём измерениям AI-оценки (замечание 17.09.2026).

Процент и вердикт из трёх значений («идти», «с оговорками», «не идти») требуют чтения;
пользователю в списке нужен знак, понятный с первого взгляда. Модель получает три
измерения — Историю, Задачу, Компетенции — с комментариями и обоснованиями, пишет для
себя сводку (`decision_summary`, пользователю не показывается) и по ней выносит
двоичное решение `decision`: участвовать или нет. `NULL` — решение не выносилось (оценка
посчитана до этой правки или модель не ответила); интерфейс показывает это отдельным
серым знаком, а не как «нет».

Revision ID: 0047_ai_decision
Revises: 0046_job_payload
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_ai_decision"
down_revision: Union[str, None] = "0046_job_payload"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_profile_scores", sa.Column("decision", sa.Boolean(), nullable=True))
    op.add_column("ai_profile_scores", sa.Column("decision_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_profile_scores", "decision_summary")
    op.drop_column("ai_profile_scores", "decision")
