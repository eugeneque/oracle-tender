"""regions and federal districts (Приложения G, H ТЗ)

Revision ID: 0001_regions_and_districts
Revises:
Create Date: 2026-08-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.seed.regions_data import FEDERAL_DISTRICTS, REGIONS

revision: str = "0001_regions_and_districts"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    federal_districts = op.create_table(
        "federal_districts",
        sa.Column("code", sa.SmallInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
    )

    regions = op.create_table(
        "regions",
        sa.Column("code", sa.String(length=2), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column(
            "federal_district_code",
            sa.SmallInteger(),
            sa.ForeignKey("federal_districts.code"),
            nullable=False,
        ),
    )

    op.bulk_insert(
        federal_districts,
        [{"code": code, "name": name} for code, name in FEDERAL_DISTRICTS],
    )
    op.bulk_insert(
        regions,
        [
            {"code": code, "name": name, "federal_district_code": fd_code}
            for code, name, fd_code in REGIONS
        ],
    )


def downgrade() -> None:
    op.drop_table("regions")
    op.drop_table("federal_districts")
