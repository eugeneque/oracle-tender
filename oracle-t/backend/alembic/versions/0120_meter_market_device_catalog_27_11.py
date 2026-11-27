"""«Рынок ПУ»: справочник приборов с доказательствами и проверкой характеристик (27.11.2026).

Тип получает собственную копию «Описания типа» для догрузки производителя из ФГИС, а каждая
характеристика модификации — источник, статус проверки и цитату. Прежние значения остаются без
строк проверки и не становятся массово неподтверждёнными.

Revision ID: 0120_meter_market_device_catalog_27_11
Revises: 0119_meter_market_energomera_26_11
Create Date: 2026-11-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0120_device_catalog"
down_revision: Union[str, None] = "0119_energomera_26_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meter_market_types", sa.Column("description_type_url", sa.Text(), nullable=True))
    op.add_column("meter_market_types", sa.Column("description_type_mirror_url", sa.Text(), nullable=True))
    op.add_column("meter_market_types", sa.Column("description_type_version", sa.String(length=20), nullable=True))
    op.add_column("meter_market_types", sa.Column("description_type_text", sa.Text(), nullable=True))
    op.add_column(
        "meter_market_types", sa.Column("description_type_fetched_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "meter_market_modification_attribute_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("modification_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["modification_id"], ["meter_market_modifications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("modification_id", "field_name", name="uq_meter_market_attribute_review_field"),
    )
    op.create_index(
        "ix_meter_market_modification_attribute_reviews_modification_id",
        "meter_market_modification_attribute_reviews",
        ["modification_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_meter_market_modification_attribute_reviews_modification_id",
        table_name="meter_market_modification_attribute_reviews",
    )
    op.drop_table("meter_market_modification_attribute_reviews")
    for name in (
        "description_type_fetched_at",
        "description_type_text",
        "description_type_version",
        "description_type_mirror_url",
        "description_type_url",
    ):
        op.drop_column("meter_market_types", name)
