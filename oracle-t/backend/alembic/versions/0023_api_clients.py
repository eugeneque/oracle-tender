"""Ключи доступа внешних систем к API (раздел 5.10 ТЗ, Этап 13 — задел под Bitrix24).

Revision ID: 0023_api_clients
Revises: 0022_log_clock_timestamp
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_api_clients"
down_revision: Union[str, None] = "0022_log_clock_timestamp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Префикс ключа виден администратору в списке; сам ключ хранится только хешем —
        # показать его повторно нельзя, при утере выпускается новый.
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Поиск при проверке ключа идёт по префиксу: перебирать все ключи с медленным argon2
    # означало бы секунды на каждый запрос интеграции.
    op.create_index("ix_api_clients_key_prefix", "api_clients", ["key_prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_clients_key_prefix", table_name="api_clients")
    op.drop_table("api_clients")
