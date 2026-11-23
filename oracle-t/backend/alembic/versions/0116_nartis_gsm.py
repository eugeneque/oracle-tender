"""«Рынок ПУ»: у НАРТИС-И100/И300 G/n — GSM (раздел 122 ARCHITECTURE.md).

0114 завела таблицу кодов НАРТИС со строкой «G/n = ? GSM» (канал зависит от модуля: G/5 — NB-IoT), и ~60 написаний
с G/1, G/2 ушли на согласование. Решение 23.11.2026: остальные модули G/n — GSM, как в описании типа. Строка
заменяется только там, где её не правил аналитик; написания пересчитываются.

Revision ID: 0116_nartis_gsm
Revises: 0115_mirtek_p_and_d
Create Date: 2026-11-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0116_nartis_gsm"
down_revision: Union[str, None] = "0115_mirtek_p_and_d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services.meter_market_interface import apply_nartis_gsm

        apply_nartis_gsm(conn)


def downgrade() -> None:
    pass
