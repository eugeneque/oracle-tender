"""zakazrf adapter implemented; fabrikant reclassified as Next.js/RSC (Playwright tier)

Revision ID: 0006_zakazrf_adapter_implemented
Revises: 0005_source_notes_update
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_zakazrf_adapter_implemented"
down_revision: Union[str, None] = "0005_source_notes_update"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sources = sa.table(
    "sources",
    sa.column("key", sa.String),
    sa.column("adapter_key", sa.String),
    sa.column("adapter_status", sa.String),
    sa.column("note", sa.String),
)


def upgrade() -> None:
    op.execute(
        sources.update()
        .where(sources.c.key == "zakazrf")
        .values(adapter_key="zakazrf", adapter_status="implemented", note=None)
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "fabrikant")
        .values(
            note="В очереди на реализацию (Этап 12 ТЗ). При исследовании выяснилось, что"
            " площадка построена на Next.js с RSC (React Server Components, поток данных"
            " сериализован не как обычный JSON) — потребует Playwright, простым HTML-парсингом"
            " не обойтись, как изначально предполагалось."
        )
    )


def downgrade() -> None:
    op.execute(
        sources.update()
        .where(sources.c.key == "zakazrf")
        .values(
            adapter_key=None,
            adapter_status="not_implemented",
            note="В очереди на реализацию (Этап 12 ТЗ).",
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "fabrikant")
        .values(note="В очереди на реализацию (Этап 12 ТЗ).")
    )
