"""Модель ИИ для матрицы соответствия — отдельный выбор администратора (раздел 104 ARCHITECTURE.md).

Матрица — 89% токенов ИИ (детализация 14.10–12.11.2026), а по фактическим счетам Qwen через
RouterAI в 15–40 раз дешевле YandexGPT Pro. Переводить матрицу целиком, не сравнив качество,
нельзя, поэтому выбор оставлен администратору: `NULL` — матрица идёт той же моделью, что и
весь разбор (как раньше), иначе — выбранной, независимо от личного выбора и авторазбора.

Revision ID: 0102_matrix_provider
Revises: 0101_ai_cost_control
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0102_matrix_provider"
down_revision: Union[str, None] = "0101_ai_cost_control"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_provider_settings", sa.Column("matrix_provider", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_provider_settings", "matrix_provider")
