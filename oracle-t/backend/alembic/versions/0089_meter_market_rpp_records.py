"""История записей реестра российской промышленной продукции по модификациям рынка ПУ
(раздел 79 ARCHITECTURE.md).

Опрос ГИСП 29.10.2026 показал: у одного исполнения несколько записей во времени —
НАРТИС-И100-W112-2 внесён в 2023 году с 83 баллами, 12.11.2024 — с 95, 14.05.2026 — со 113.
Одно поле «баллы» у модификации отвечает только на вопрос «как сейчас», а экран локализации
считает по кварталам: поверка 1 кв. 2026 года соответствовала требованиям 2024-го (95 баллов),
а не 2025-го. Поэтому записи хранятся списком, и поверка относится к записи, действовавшей на
дату поверки; поля модификации (`in_rpp`, `score`, `rpp_included_at`) — сводка «на сегодня».

Revision ID: 0089_meter_market_rpp_records
Revises: 0088_meter_market_source
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0089_meter_market_rpp_records"
down_revision: Union[str, None] = "0088_meter_market_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "meter_market_rpp_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "modification_id",
            sa.Integer(),
            sa.ForeignKey("meter_market_modifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_number", sa.String(100), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("source", sa.String(10), nullable=False, server_default="gisp"),
    )
    op.create_index("ix_meter_market_rpp_records_modification_id", "meter_market_rpp_records", ["modification_id"])


def downgrade() -> None:
    op.drop_table("meter_market_rpp_records")
