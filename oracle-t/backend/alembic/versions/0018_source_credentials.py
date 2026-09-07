"""учётные данные площадок-источников (раздел 4.1, 5.1 ТЗ)

Пароль хранится шифротекстом Fernet (`app/core/crypto.py`), ключ — в файле вне БД.
Несколько учёток на источник разрешены (у площадки бывает по учётке на юрлицо), поэтому
уникальность — по паре (source_id, label), а не по одному source_id.

Revision ID: 0018_source_credentials
Revises: 0017_tender_history
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_source_credentials"
down_revision: Union[str, None] = "0017_tender_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=150), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.UniqueConstraint("source_id", "label", name="uq_source_credentials_source_label"),
    )
    op.create_index("ix_source_credentials_source_id", "source_credentials", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_source_credentials_source_id", table_name="source_credentials")
    op.drop_table("source_credentials")
