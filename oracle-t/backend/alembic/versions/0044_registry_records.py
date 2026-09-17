"""Реестры допуска продукции: ПП 719 (ГИСП) и ЗАК ПАО «Россети» (замечание тестировщика
16.09.2026).

Таблица `product_registry_records` — записи о допуске модели в реестрах с номером, датами
и признаком «проверено: записи нет». Два реестра заводятся источниками типа
`admission_registry` — для мониторинга доступности и официальной ссылки; опросом тендеров
пропускаются. См. `app/models/registry_record.py`.

Revision ID: 0044_registry_records
Revises: 0043_tender_bookmarks
Create Date: 2026-09-17
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.seed.sources_data import ADMISSION_REGISTRY_SOURCES

revision: str = "0044_registry_records"
down_revision: Union[str, None] = "0043_tender_bookmarks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sources = sa.table(
    "sources",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("key", sa.String),
    sa.column("name", sa.String),
    sa.column("url", sa.String),
    sa.column("type", sa.String),
    sa.column("status", sa.String),
    sa.column("polling_schedule", sa.String),
    sa.column("adapter_key", sa.String),
    sa.column("adapter_status", sa.String),
    sa.column("note", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "product_registry_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("registry", sa.String(40), nullable=False),
        sa.Column("presence", sa.String(10), nullable=False, server_default="present"),
        sa.Column("record_number", sa.String(100), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("verified_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "product_id", "registry", name="uq_product_registry_records_product_registry"
        ),
    )
    op.create_index(
        "ix_product_registry_records_product_id", "product_registry_records", ["product_id"]
    )

    op.bulk_insert(
        sources,
        [
            {
                "id": uuid.uuid4(),
                "key": key,
                "name": name,
                "url": url,
                "type": type_,
                "status": "active",
                "polling_schedule": schedule,
                "adapter_key": adapter_key,
                "adapter_status": adapter_status,
                "note": note,
            }
            for key, name, url, type_, adapter_key, adapter_status, schedule, note in ADMISSION_REGISTRY_SOURCES
        ],
    )


def downgrade() -> None:
    op.drop_table("product_registry_records")
    op.execute(
        sources.delete().where(sources.c.key.in_([row[0] for row in ADMISSION_REGISTRY_SOURCES]))
    )
