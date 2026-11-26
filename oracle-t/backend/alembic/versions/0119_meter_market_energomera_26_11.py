"""«Рынок ПУ»: ПП 890 и интерфейсы Энергомеры по написанию (26.11.2026).

SPDs с точным регистром означает соответствие ПП 890, IEC — несоответствие. Коды GB04,
LG14, NBO1/NBO2 и показанное аналитиком CE308 S34 для Грузии получают однозначный интерфейс
на уровне написания, а не общей модификации. Ручные решения не перезаписываются; повторный
запуск безопасен.

Revision ID: 0119_meter_market_energomera_26_11
Revises: 0118_meter_market_analytics_24_11
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0119_energomera_26_11"
down_revision: Union[str, None] = "0118_market_24_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services import meter_market_interface as interface_rules
        from app.services import meter_market_rules as rules

        rules.apply_energomera_smart(conn)
        interface_rules.apply_energomera_interfaces(conn)


def downgrade() -> None:
    pass
