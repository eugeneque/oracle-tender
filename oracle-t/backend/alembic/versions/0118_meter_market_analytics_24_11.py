"""«Рынок ПУ»: правки аналитика 24.11.2026 — НАРТИС, интерфейсы и ПП 890 МИРТЕК.

1. Написания НАРТИС с «-2» приводятся к локализованным исполнениям W112-2/W113-2/W132-2/W133-2,
   а записанные под чужим номером ГРСИ Р1/Р3/И100 переносятся в типы 97366-25/97367-25/86199-22.
2. МИРТЕК-12-РУ получает таблицу кодов интерфейсов: RS485 — RS, RF433/RF2400 — RF, G/1 — GSM,
   G/5 — NB-IoT, MOD — mod; W2/W3/SP1 — отдельные модификации с исполнением.
3. ПП 890 МИРТЕК решает протокол Pn: строка с P2 без завершающего D соответствует (D не дописал
   поверитель), D без Pn — нет. След массового `manual` 0109 у небазовых модификаций снимается;
   пересчитываются только значения с источником `rule`.

Повторный запуск безопасен; ручные решения не перезаписываются.

Revision ID: 0118_meter_market_analytics_24_11
Revises: 0117_nartis_rf2400
Create Date: 2026-11-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0118_market_24_11"
down_revision: Union[str, None] = "0117_nartis_rf2400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services import meter_market_interface as interface_rules
        from app.services import meter_market_rules as rules

        rules.apply_nartis_corrections(conn)
        rules.apply_mirtek_body_mods(conn)
        rules.apply_mirtek_stale_blanket_smart(conn)
        interface_rules.apply_mirtek_codes(conn)
        interface_rules.apply_interface_codes(conn)
        rules.apply_mirtek_protocol(conn)


def downgrade() -> None:
    pass
