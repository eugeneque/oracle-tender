"""Переключатель ИИ-провайдера и учётные данные Claude через RouterAI (просьба заказчика
18.09.2026).

До этого ИИ-модуль работал только через YandexGPT. Теперь активный провайдер выбирается в
«Настройки → Интеграции»; все запросы генерации идут через выбранного, прежний не вызывается.
Ключ RouterAI, как и ключ Yandex, хранится в БД и настраивается из интерфейса, а не в .env.

Таблица — синглтон (одна строка), строка заводится здесь же с `active_provider = 'yandex'`,
чтобы после обновления поведение системы не изменилось до сознательного переключения.

Revision ID: 0049_ai_provider_settings
Revises: 0048_manufacturer_market_share
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_ai_provider_settings"
down_revision: Union[str, None] = "0048_manufacturer_market_share"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Совпадает с `_SINGLETON_ID` в app/services/ai_provider_service.py.
_SINGLETON_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "ai_provider_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("active_provider", sa.String(length=20), nullable=False, server_default="yandex"),
        sa.Column("routerai_api_key", sa.Text(), nullable=True),
        sa.Column("routerai_model", sa.String(length=100), nullable=True),
        sa.Column("routerai_base_url", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.execute(
        f"INSERT INTO ai_provider_settings (id, active_provider) VALUES ('{_SINGLETON_ID}', 'yandex')"
    )


def downgrade() -> None:
    op.drop_table("ai_provider_settings")
