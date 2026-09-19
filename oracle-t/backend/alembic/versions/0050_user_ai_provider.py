"""Персональный выбор модели ИИ (просьба заказчика 18.09.2026).

Переключатель провайдера из 0049 был один на систему; заказчик хочет, чтобы каждый
пользователь выбирал модель сам — один работает с Claude, другой с YandexGPT. Выбор хранится
у пользователя; `NULL` означает «не выбирал», и тогда действует системная модель по умолчанию
из `ai_provider_settings` — она же для задач по расписанию, у которых автора нет.

Revision ID: 0050_user_ai_provider
Revises: 0049_ai_provider_settings
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050_user_ai_provider"
down_revision: Union[str, None] = "0049_ai_provider_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ai_provider", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ai_provider")
