"""Bitrix24: настройки подключения и связь «тендер → сделка» (08.10.2026).

До этого интеграция ограничивалась CSV лидов для ручного импорта. Заказчик завёл на портале
воронку «Тендеры — тест ИИ» со стадией «Парсинг опубликованных тендеров» и попросил класть
туда тендеры сделками через входящий вебхук. Здесь две таблицы:

- `bitrix24_settings` — синглтон: вебхук (зашифрован), воронка, стадия и выключатель
  отправки. Выключатель по умолчанию выключен — портал боевой, тестового нет;
- `bitrix_deal_links` — какая сделка заведена под тендер, чтобы повторная отправка
  обновляла её, а не создавала дубль.

Revision ID: 0070_bitrix24_deals
Revises: 0069_okpd2_electric_meters
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070_bitrix24_deals"
down_revision: Union[str, None] = "0069_okpd2_electric_meters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bitrix24_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("stage_id", sa.String(50), nullable=False, server_default="C5:PARSING"),
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_status", sa.String(20), nullable=True),
        sa.Column("last_check_message", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
    )
    op.create_table(
        "bitrix_deal_links",
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "pushed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_bitrix_deal_links_deal_id", "bitrix_deal_links", ["deal_id"])


def downgrade() -> None:
    op.drop_index("ix_bitrix_deal_links_deal_id", table_name="bitrix_deal_links")
    op.drop_table("bitrix_deal_links")
    op.drop_table("bitrix24_settings")
