"""sources and tenders (раздел 4, 5.1, 7, 9 Этап 2 ТЗ)

Revision ID: 0003_sources_and_tenders
Revises: 0002_users_and_logs
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.seed.sources_data import SOURCES

revision: str = "0003_sources_and_tenders"
down_revision: Union[str, None] = "0002_users_and_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sources = op.create_table(
        "sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(length=50), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column(
            "polling_schedule", sa.String(length=30), nullable=False, server_default="twice_daily"
        ),
        sa.Column("adapter_key", sa.String(length=50), nullable=True),
        sa.Column(
            "adapter_status", sa.String(length=30), nullable=False, server_default="not_implemented"
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sources_key", "sources", ["key"])

    op.create_table(
        "tenders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.String(length=500), nullable=True),
        sa.Column("organizer_name", sa.String(length=500), nullable=True),
        sa.Column("procurement_method", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("price", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="RUB"),
        sa.Column("application_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("region_organizer_code", sa.String(length=2), sa.ForeignKey("regions.code"), nullable=True),
        sa.Column("region_delivery_code", sa.String(length=2), sa.ForeignKey("regions.code"), nullable=True),
        sa.Column("federal_district_code", sa.SmallInteger(), sa.ForeignKey("federal_districts.code"), nullable=True),
        sa.Column("okpd2_code", sa.String(length=20), nullable=True),
        sa.Column("tender_type", sa.String(length=30), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("ai_comment", sa.Text(), nullable=True),
        sa.Column("relevance_status", sa.String(length=20), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_tenders_source_external_id"),
    )
    op.create_index("ix_tenders_source_id", "tenders", ["source_id"])
    op.create_index("ix_tenders_external_id", "tenders", ["external_id"])
    op.create_index("ix_tenders_okpd2_code", "tenders", ["okpd2_code"])

    op.bulk_insert(
        sources,
        [
            {
                "key": key,
                "name": name,
                "url": url,
                "type": type_,
                "adapter_key": key if adapter_status == "implemented" else None,
                "adapter_status": adapter_status,
                "note": note,
            }
            for key, name, url, type_, adapter_status, note in SOURCES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_tenders_okpd2_code", table_name="tenders")
    op.drop_index("ix_tenders_external_id", table_name="tenders")
    op.drop_index("ix_tenders_source_id", table_name="tenders")
    op.drop_table("tenders")
    op.drop_index("ix_sources_key", table_name="sources")
    op.drop_table("sources")
