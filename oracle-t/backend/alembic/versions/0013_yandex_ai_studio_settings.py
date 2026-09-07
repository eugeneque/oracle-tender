"""yandex_ai_studio_settings (раздел 5.4 ТЗ, ИИ-модуль + fallback-OCR раздела 5.2 —
ключ хранится в БД и настраивается из админ-панели, а не в .env, по запросу заказчика)

Revision ID: 0013_yandex_ai_studio_settings
Revises: 0012_lot_online_implemented
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_yandex_ai_studio_settings"
down_revision: Union[str, None] = "0012_lot_online_implemented"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "yandex_ai_studio_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.String(length=50), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("yandex_ai_studio_settings")
