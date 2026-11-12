"""Удаление «Автоматического разбора» (раздел 105 ARCHITECTURE.md).

Авторазбор (раздел 81) включался под бесплатный YandexGPT и 30.10.2026 — в первый платный
день — разобрал 69 закупок за 11 часов: 76% платных токенов YandexGPT за 30.10–12.11.2026.
Решение заказчика 12.11.2026: закупки разбираются только по кнопке, авторазбора в системе нет.
Колонки флага, модели и лимита удаляются вместе с ним.

Revision ID: 0103_drop_auto_review
Revises: 0102_matrix_provider
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0103_drop_auto_review"
down_revision: Union[str, None] = "0102_matrix_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ai_provider_settings", "auto_review_daily_limit")
    op.drop_column("ai_provider_settings", "auto_review_provider")
    op.drop_column("ai_provider_settings", "auto_review_enabled")


def downgrade() -> None:
    op.add_column(
        "ai_provider_settings",
        sa.Column("auto_review_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ai_provider_settings",
        sa.Column("auto_review_provider", sa.String(20), nullable=False, server_default="qwen"),
    )
    op.add_column(
        "ai_provider_settings",
        sa.Column("auto_review_daily_limit", sa.Integer(), nullable=False, server_default="10"),
    )
