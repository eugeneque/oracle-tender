"""Тайпит, Нартис и Ротек как источники справочника продукции

Вторая волна сайтов конкурентов. Отдельной миграцией, а не правкой 0032, по той же причине,
что и 0032 не правила 0031: на базах, где предыдущая уже применена, повторное сидирование
упало бы на уникальном `sources.key`.

Revision ID: 0034_wave2_catalog_sources
Revises: 0033_product_model_code
Create Date: 2026-09-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.seed.sources_data import COMPETITOR_CATALOG_SOURCES_WAVE2

revision: str = "0034_wave2_catalog_sources"
down_revision: Union[str, None] = "0033_product_model_code"
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
            for key, name, url, type_, adapter_key, adapter_status, schedule, note in COMPETITOR_CATALOG_SOURCES_WAVE2
        ],
    )


def downgrade() -> None:
    keys = tuple(item[0] for item in COMPETITOR_CATALOG_SOURCES_WAVE2)
    op.execute(
        sa.text("DELETE FROM sources WHERE key IN :keys").bindparams(
            sa.bindparam("keys", value=keys, expanding=True)
        )
    )
