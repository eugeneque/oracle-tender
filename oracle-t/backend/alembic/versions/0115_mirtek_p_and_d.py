"""«Рынок ПУ»: ПП 890 у МИРТЕК — нужны и протокол Pn, и признак D (раздел 123 ARCHITECTURE.md).

Аналитик 24.11.2026: «P и D — нужны оба». Правило `meter_market_rules.protocol_verdict` изменилось
(было «достаточно любого»), значения `meter_market_variants.smart_890` с источником `rule` пересчитываются.
Ручные решения не трогаются. Повторный запуск безопасен; откат данных не предусмотрен.

Revision ID: 0115_mirtek_p_and_d
Revises: 0114_variant_interface
Create Date: 2026-11-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0115_mirtek_p_and_d"
down_revision: Union[str, None] = "0114_variant_interface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services.meter_market_rules import apply_mirtek_protocol

        apply_mirtek_protocol(conn)


def downgrade() -> None:
    pass
