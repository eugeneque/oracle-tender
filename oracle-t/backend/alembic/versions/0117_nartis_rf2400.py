"""«Рынок ПУ»: у НАРТИС-И100/И300 «RF2400» без номера модуля — ZigBee (раздел 122 ARCHITECTURE.md).

Написания вида «…-SS-RF2400», «…-RS485-RF2400-P1-…» (номер модуля 2400 МГц не указан) уходили на согласование:
кода «RF2400» в таблице не было. Решение 23.11.2026: это ZigBee. Строка дописывается в таблицы, заведённые 0114,
если аналитик ещё не задал свою; написания пересчитываются.

Revision ID: 0117_nartis_rf2400
Revises: 0116_nartis_gsm
Create Date: 2026-11-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0117_nartis_rf2400"
down_revision: Union[str, None] = "0116_nartis_gsm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services.meter_market_interface import apply_nartis_rf2400

        apply_nartis_rf2400(conn)


def downgrade() -> None:
    pass
