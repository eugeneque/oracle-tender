"""Интеграция с rusprofile.ru (просьба заказчика 18.09.2026).

Под учётной записью заказчика сайт отдаёт полный список госзакупок компании — с проигрышами,
которых нет ни в одном другом открытом источнике, — лицензии, численность, финансы. Раздел
«Моя компания» заполняется с сайта, а не руками.

Три изменения:

* `rusprofile_settings` — логин/пароль и итог последней синхронизации (синглтон, как и
  остальные настройки интеграций);
* `company_profile` — номер карточки, досье целиком (JSONB) и время синхронизации;
* `company_participations.source` — новое значение `rusprofile` (колонка строковая, ограничения
  на значения нет, менять схему не нужно).

Revision ID: 0051_rusprofile_integration
Revises: 0050_user_ai_provider
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_rusprofile_integration"
down_revision: Union[str, None] = "0050_user_ai_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Совпадает с `_SINGLETON_ID` в app/services/rusprofile_service.py.
_SINGLETON_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "rusprofile_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("login", sa.String(length=200), nullable=True),
        sa.Column("password", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),
        sa.Column("last_sync_message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.execute(f"INSERT INTO rusprofile_settings (id) VALUES ('{_SINGLETON_ID}')")

    op.add_column("company_profile", sa.Column("rusprofile_card_id", sa.String(length=20), nullable=True))
    op.add_column(
        "company_profile",
        sa.Column("rusprofile_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "company_profile",
        sa.Column("rusprofile_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_profile", "rusprofile_synced_at")
    op.drop_column("company_profile", "rusprofile_data")
    op.drop_column("company_profile", "rusprofile_card_id")
    op.drop_table("rusprofile_settings")
