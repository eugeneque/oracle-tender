"""ПО верхнего уровня: списки поддерживаемого оборудования (замечание тестировщика 16.09.2026).

Требование «интеграция в ПО верхнего уровня» (Пирамида, Энфорс, Энергосфера, яЭнергетик,
АльфаЦЕНТР, Некта, ЛЭРС) стоит в ТЗ закупок регулярно, и ответить на него по каталогу
производителя нельзя: интегрирован прибор или нет, публикует разработчик ПО. Здесь:

* семь площадок заводятся источниками типа `upper_software` — видны и управляются в
  общем разделе «Источники», опросом тендеров пропускаются (`CATALOG_SOURCE_TYPES`);
* таблица `upper_software_devices` — записи списков как на сайте плюс результат разбора;
* таблица `upper_software_product_links` — связь записей с моделями справочника.

Первичное наполнение миграция не делает: списки читаются еженедельной задачей и кнопкой
из каталога — на проде первый прогон полезнее увидеть с отчётом, чем получить таблицу,
заполненную втёмную при накате.

Revision ID: 0042_upper_software
Revises: 0041_manual_request_source
Create Date: 2026-09-16
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.seed.sources_data import UPPER_SOFTWARE_SOURCES

revision: str = "0042_upper_software"
down_revision: Union[str, None] = "0041_manual_request_source"
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
        "upper_software_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", sa.String(255), nullable=False),
        sa.Column("device_raw", sa.Text(), nullable=False),
        sa.Column("device_names", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("manufacturer_raw", sa.Text(), nullable=True),
        sa.Column("si_codes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("device_type", sa.String(100), nullable=True),
        sa.Column("is_generic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("manufacturer_matched_by", sa.String(30), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source_id", "fingerprint", name="uq_upper_software_devices_fingerprint"),
    )
    op.create_index(
        "ix_upper_software_devices_source_id", "upper_software_devices", ["source_id"]
    )
    op.create_index(
        "ix_upper_software_devices_manufacturer_id", "upper_software_devices", ["manufacturer_id"]
    )

    op.create_table(
        "upper_software_product_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("upper_software_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("matched_by", sa.String(30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("device_id", "product_id", name="uq_upper_software_product_links_pair"),
    )
    op.create_index(
        "ix_upper_software_product_links_device_id", "upper_software_product_links", ["device_id"]
    )
    op.create_index(
        "ix_upper_software_product_links_product_id", "upper_software_product_links", ["product_id"]
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
            for key, name, url, type_, adapter_key, adapter_status, schedule, note in UPPER_SOFTWARE_SOURCES
        ],
    )


def downgrade() -> None:
    op.drop_table("upper_software_product_links")
    op.drop_table("upper_software_devices")
    op.execute(
        sources.delete().where(sources.c.key.in_([row[0] for row in UPPER_SOFTWARE_SOURCES]))
    )
