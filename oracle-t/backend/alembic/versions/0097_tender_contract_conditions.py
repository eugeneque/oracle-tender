"""Условия договора и обеспечения для Bitrix24 (11.11.2026).

Перечень «Что еще нужно от ИИ» (пп. 4, 6, 16–27, 30–31, 95): раньше условия договора уходили
в портал списком коротких строк без вида дней, события-основания, суммы, процента и цитаты.
Структурный разбор хранится одним JSONB-документом на закупку и заменяется целиком при
каждом разборе документации — как условия поставки (`0095`).

Revision ID: 0097_tender_contract_conditions
Revises: 0096_meter_market_smart_890
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0097_tender_contract_conditions"
down_revision: Union[str, None] = "0096_meter_market_smart_890"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_contract_conditions",
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tender_id"),
    )


def downgrade() -> None:
    op.drop_table("tender_contract_conditions")
