"""Модель поставки и исполнение договора (05.11.2026).

Bitrix24 1.7.0 принимает в разделе `contract` строки `supply_model`, `delivery_point`,
`delivery_schedule` и `delivery_request_terms`. Извлечённое моделью хранится одним
JSONB-документом на закупку и заменяется целиком при каждом разборе документации.

Revision ID: 0095_tender_delivery_terms
Revises: 0094_tender_event_data
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0095_tender_delivery_terms"
down_revision: Union[str, None] = "0094_tender_event_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_delivery_terms",
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tender_id"),
    )


def downgrade() -> None:
    op.drop_table("tender_delivery_terms")
