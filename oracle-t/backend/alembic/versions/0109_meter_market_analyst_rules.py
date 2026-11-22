"""«Рынок ПУ»: правила аналитика по ПРОМЭНЕРГО и МИРТЕК для всех периодов (раздел 114 ARCHITECTURE.md).

Замечания аналитика к 3 кв. 2026 просили распространить на все периоды: счётчики газа под номером
описания типа МИРТЕК не учитывать (`meter_market_variants.excluded_reason`), типы МИРТЕК — ПП 890
«соответствует», поверки «Нет модификации» с буквой U в номере — «Умному учёту», написания i-prom —
к исполнениям справочника с РПП от 26.02.2025. Сами правила — `meter_market_rules.apply_all`
(повторный запуск безопасен; вручную — `python -m app.services.meter_market_rules`).

Revision ID: 0109_meter_market_analyst_rules
Revises: 0108_isu_profile_keyword
Create Date: 2026-11-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0109_meter_market_analyst_rules"
down_revision: Union[str, None] = "0108_isu_profile_keyword"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meter_market_variants", sa.Column("excluded_reason", sa.Text(), nullable=True))
    # Данные справочника есть не везде (чистая база, тесты): без таблицы поверок правила не нужны.
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        # Поштучно, а не `apply_all`: позднейшие правила (0112) опираются на колонки, которых здесь ещё нет.
        from app.services import meter_market_rules as rules

        rules.apply_gas_exclusion(conn)
        rules.apply_smart_unit_split(conn)
        rules.apply_iprom(conn)
        rules.apply_mirtek_smart(conn)


def downgrade() -> None:
    op.drop_column("meter_market_variants", "excluded_reason")
