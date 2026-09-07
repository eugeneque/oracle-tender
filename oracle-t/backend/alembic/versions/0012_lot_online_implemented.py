"""lot_online adapter implemented (public JSON API, no Playwright needed)

Revision ID: 0012_lot_online_implemented
Revises: 0011_tektorg_implemented
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_lot_online_implemented"
down_revision: Union[str, None] = "0011_tektorg_implemented"
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
        .where(sources.c.key == "lot_online")
        .values(adapter_key="lot_online", adapter_status="implemented", note=None)
    )


def downgrade() -> None:
    op.execute(
        sources.update()
        .where(sources.c.key == "lot_online")
        .values(
            adapter_key=None,
            adapter_status="not_implemented",
            note="В очереди на реализацию (Этап 12 ТЗ). При исследовании выяснилось, что"
            " площадка построена на Angular (`<app-root>`) — потребует Playwright.",
        )
    )
