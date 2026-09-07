"""Уведомления по почте и их журнал (раздел 5.8 ТЗ).

Revision ID: 0021_notifications
Revises: 0020_background_jobs
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_notifications"
down_revision: Union[str, None] = "0020_background_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Канал выключен, пока администратор не заполнит ящик и не включит его сам:
        # рассылка, начавшаяся сама по себе после обновления, — худший из сценариев.
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"),
        sa.Column("smtp_security", sa.String(length=10), nullable=False, server_default="ssl"),
        sa.Column("smtp_username", sa.String(length=255), nullable=True),
        # Пароль шифруется ключом .credentials_key (app/core/crypto.py) — как и пароли площадок.
        sa.Column("smtp_password_encrypted", sa.Text(), nullable=True),
        sa.Column("from_address", sa.String(length=255), nullable=True),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("admin_recipients", sa.Text(), nullable=True),
        sa.Column(
            "trigger_new_relevant", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("trigger_high_win", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "trigger_deadline_soon", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "trigger_critical_error", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        # Пороги раздела 5.8 ТЗ: 80% по проценту победителя, 5 дней до окончания подачи.
        sa.Column("win_percentage_threshold", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("deadline_days_threshold", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )

    op.create_table(
        "notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="email"),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # Тендер удалён — запись об отправленном письме остаётся: это история переписки,
        # а не производная от тендера.
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_trigger", "notifications", ["trigger"])
    op.create_index("ix_notifications_status", "notifications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index("ix_notifications_trigger", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("notification_settings")
