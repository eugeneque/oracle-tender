"""Сайты производителей-конкурентов как источники справочника продукции

Энергомера, КПЗ и Промэнерго заводятся источниками наравне с сайтом МИРТЕК (миграция 0031):
их каталоги обходятся тем же сервисом и по тому же расписанию, а видны и управляются в общем
разделе «Источники».

Отдельная миграция, а не правка 0031: на базах, где 0031 уже применена, повторный прогон
сидирования создал бы дубли по уникальному `sources.key`.

Revision ID: 0032_competitor_catalog_sources
Revises: 0031_catalog_sources_and_queue
Create Date: 2026-09-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.seed.sources_data import COMPETITOR_CATALOG_SOURCES

revision: str = "0032_competitor_catalog_sources"
down_revision: Union[str, None] = "0031_catalog_sources_and_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sources = sa.table(
        "sources",
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
    op.bulk_insert(
        sources,
        [
            {
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
            for key, name, url, type_, adapter_key, adapter_status, schedule, note in COMPETITOR_CATALOG_SOURCES
        ],
    )


def downgrade() -> None:
    keys = tuple(item[0] for item in COMPETITOR_CATALOG_SOURCES)
    op.execute(
        sa.text("DELETE FROM sources WHERE key IN :keys").bindparams(
            sa.bindparam("keys", value=keys, expanding=True)
        )
    )
