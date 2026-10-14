"""Учёт расхода токенов ИИ и окно контекста по моделям (14.10.2026).

* `ai_usage_records` — каждое обращение к модели: провайдер, модель, шаг разбора, токены
  (входящие, исходящие, из кэша), длительность, исход. Руководство спросило, сколько Сова
  тратит и сколько потребует ускоритель с окном 32 тыс. токенов; оценка «на глаз»
  расходилась в разы, а провайдеры возвращают точный счётчик в каждом ответе. Таблицу
  показывает администратору «Интеграции → Детализация ИИ».
* `ai_provider_settings.model_limits` — окно контекста и потолок ответа по каждой модели.
  До этого предел карточки производителя (70 тыс. символов) и потолок ответа (16 тыс.
  токенов) были зашиты в код под YandexGPT и Claude; на модели с окном 32 тыс. второе
  вместе с первым в окно не помещалось.

Revision ID: 0074_ai_usage_and_limits
Revises: 0073_catalog_data_quality
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074_ai_usage_and_limits"
down_revision: Union[str, None] = "0073_catalog_data_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_kind", sa.String(40), nullable=True),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prompt_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost", sa.Numeric(14, 6), nullable=True),
    )
    op.create_index("ix_ai_usage_records_created_at", "ai_usage_records", ["created_at"])
    op.create_index("ix_ai_usage_records_tender_id", "ai_usage_records", ["tender_id"])
    op.add_column(
        "ai_provider_settings",
        sa.Column(
            "model_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_provider_settings", "model_limits")
    op.drop_index("ix_ai_usage_records_tender_id", table_name="ai_usage_records")
    op.drop_index("ix_ai_usage_records_created_at", table_name="ai_usage_records")
    op.drop_table("ai_usage_records")
