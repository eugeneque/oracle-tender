"""source availability ping fields (доступность площадок, раз в минуту)

Revision ID: 0004_source_availability
Revises: 0003_sources_and_tenders
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_source_availability"
down_revision: Union[str, None] = "0003_sources_and_tenders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("availability_status", sa.String(length=20), nullable=True))
    op.add_column(
        "sources", sa.Column("availability_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sources", sa.Column("availability_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "availability_error")
    op.drop_column("sources", "availability_checked_at")
    op.drop_column("sources", "availability_status")
