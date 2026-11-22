"""«Рынок ПУ»: ПП 890 по написанию — у МИРТЕК решает протокол Pn в обозначении (раздел 120 ARCHITECTURE.md).

Аналитик (20.11.2026) отметила «не соответствует» у написаний МИРТЕК без протокола `Pn` (`…-RF433/1-HKMOQ1V3`),
а с `-P1/-P2` — «соответствует»; признак ПП 890 хранился на модификации, и «SP3» с протоколом и без него
показывались одинаково. Колонки `smart_890*` у написания главнее решения модификации (кроме её ручного
решения), значение выставляет `meter_market_rules.apply_mirtek_protocol`.

Revision ID: 0112_variant_smart_890
Revises: 0111_mirtek_smart_rollback
Create Date: 2026-11-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0112_variant_smart_890"
down_revision: Union[str, None] = "0111_mirtek_smart_rollback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meter_market_variants", sa.Column("smart_890", sa.Boolean(), nullable=True))
    op.add_column("meter_market_variants", sa.Column("smart_890_source", sa.String(length=10), nullable=True))
    op.add_column("meter_market_variants", sa.Column("smart_890_note", sa.Text(), nullable=True))
    op.add_column("meter_market_variants", sa.Column("smart_890_at", sa.DateTime(timezone=True), nullable=True))
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services.meter_market_rules import apply_mirtek_protocol

        apply_mirtek_protocol(conn)


def downgrade() -> None:
    for column in ("smart_890_at", "smart_890_note", "smart_890_source", "smart_890"):
        op.drop_column("meter_market_variants", column)
