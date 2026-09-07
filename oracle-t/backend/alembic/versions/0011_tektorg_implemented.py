"""tektorg adapter implemented (Playwright)

Revision ID: 0011_tektorg_implemented
Revises: 0010_fabrikant_implemented
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tektorg_implemented"
down_revision: Union[str, None] = "0010_fabrikant_implemented"
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
        .where(sources.c.key == "tektorg")
        .values(adapter_key="tektorg", adapter_status="implemented", note=None)
    )


def downgrade() -> None:
    op.execute(
        sources.update()
        .where(sources.c.key == "tektorg")
        .values(
            adapter_key=None,
            adapter_status="not_implemented",
            note="В очереди на реализацию (Этап 12 ТЗ). Фронтенд построен на Next.js — вероятно"
            " потребует Playwright.",
        )
    )
