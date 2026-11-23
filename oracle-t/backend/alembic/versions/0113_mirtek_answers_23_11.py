"""«Рынок ПУ»: ответы аналитика 23.11.2026 по МИРТЕК (раздел 121 ARCHITECTURE.md).

- ПП 890 по написанию: «P и D — признаки соответствия, сомнительное считать соответствующим, отклоняем
  только явные» — пересчёт `variants.smart_890` (правило `protocol_verdict` изменилось после 0112);
- чужие приборы («Ладога 0102», «Меркурий 234 …») и старые описания типа («МИРТЕК-3-РУ», «МИРТЕК-301») —
  не учитываются (`excluded_reason`), пустые модификации удаляются;
- опечатки «МИРТЕК-2232-РУ» и «МИРТЕК-323-РУ» (67661-17) — это «МИРТЕК-232-РУ».

Данные пересчитываются правилами `meter_market_rules`; откат данных не предусмотрен.

Revision ID: 0113_mirtek_answers_23_11
Revises: 0112_variant_smart_890
Create Date: 2026-11-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0113_mirtek_answers_23_11"
down_revision: Union[str, None] = "0112_variant_smart_890"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services import meter_market_rules as rules

        rules.apply_mirtek_exclusions(conn)
        rules.apply_mirtek_typos(conn)
        rules.apply_mirtek_protocol(conn)


def downgrade() -> None:
    pass
