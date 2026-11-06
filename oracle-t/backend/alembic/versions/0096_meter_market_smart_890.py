"""Соответствие модификаций ПУ постановлению 890 (06.11.2026).

Аналитик: «соответствие ППРФ 890 не равно локализации» — счётчик должен быть интеллектуальным
(раздел III постановления), а в одном описании типа бывают и интеллектуальные, и простейшие
модификации. Признак хранится у модификации; ИИ ставит его сам, ручное решение аналитика
(`smart_890_source = 'manual'`) он не перезаписывает.

Revision ID: 0096_meter_market_smart_890
Revises: 0095_tender_delivery_terms
Create Date: 2026-11-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0096_meter_market_smart_890"
down_revision: Union[str, None] = "0095_tender_delivery_terms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meter_market_modifications", sa.Column("smart_890", sa.Boolean(), nullable=True))
    op.add_column("meter_market_modifications", sa.Column("smart_890_source", sa.String(10), nullable=True))
    op.add_column("meter_market_modifications", sa.Column("smart_890_note", sa.Text(), nullable=True))
    op.add_column("meter_market_modifications", sa.Column("smart_890_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("smart_890_at", "smart_890_note", "smart_890_source", "smart_890"):
        op.drop_column("meter_market_modifications", column)
