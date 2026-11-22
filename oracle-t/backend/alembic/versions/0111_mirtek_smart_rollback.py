"""«Рынок ПУ»: откат массового «ПП 890 — соответствует» у МИРТЕК и чужих приборов-модификаций (раздел 119).

Миграция 0109 поставила «соответствует, manual» всем модификациям типов МИРТЕК — в том числе заведённым
ИИ 13.11.2026 (SP1, W2, W3, W32 RF433 …). Аналитик (20.11.2026) назвала часть из них «не соответствует»,
а «МИРТЕК-301» и другие она не размечала. Здесь:

1. у модификаций МИРТЕК, не менявшихся после 0109 (отметка 19.11.2026 до 09:00, значение «да», примечание
   правила), снимается решение — их снова оценивает ИИ или аналитик; три базовые строки остаются;
2. ИИ завёл как модификации МИРТЕК чужие приборы («Ладога 0102», «Меркурий 234 ARTM-03», «ОМЕГА ЭТК GSM G4») —
   их написания возвращаются в очередь «Разметить», пустые модификации удаляются.

Правки аналитика после 0109 (updated_at позже) не затрагиваются. Откат данных не предусмотрен.

Revision ID: 0111_mirtek_smart_rollback
Revises: 0110_bitrix_gap_sources
Create Date: 2026-11-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0111_mirtek_smart_rollback"
down_revision: Union[str, None] = "0110_bitrix_gap_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOTE = "первичная поверка: МИРТЕК производит только соответствующие ПП РФ 890 ПУ (замечание аналитика 19.11.2026)"
BASE_NAMES = ["МИРТЕК-12-РУ", "МИРТЕК-212-РУ", "МИРТЕК-232-РУ"]
FOREIGN = ["Ладога 0102", "Меркурий 234 ARTM-03", "ОМЕГА ЭТК GSM G4"]

MIRTEK_MODS = (
    "SELECT m.id FROM meter_market_modifications m JOIN meter_market_types t ON t.id = m.type_id "
    "JOIN meter_market_manufacturers f ON f.id = t.manufacturer_id WHERE f.name = 'МИРТЕК'"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE meter_market_modifications SET smart_890 = NULL, smart_890_source = NULL, smart_890_note = NULL, "
            "smart_890_at = NULL WHERE id IN (" + MIRTEK_MODS + ") AND smart_890 IS TRUE AND smart_890_source = 'manual' "
            "AND smart_890_note = :n AND updated_at < '2026-11-19 09:00' AND name <> ALL(:base)"
        ),
        {"n": NOTE, "base": BASE_NAMES},
    )
    ids = [r[0] for r in conn.execute(sa.text(MIRTEK_MODS + " AND m.name = ANY(:f)"), {"f": FOREIGN})]
    if ids:
        conn.execute(
            sa.text(
                "UPDATE meter_market_variants SET modification_id = NULL, mapped_by = NULL WHERE modification_id = ANY(:i)"
            ),
            {"i": ids},
        )
        conn.execute(sa.text("DELETE FROM meter_market_modifications WHERE id = ANY(:i)"), {"i": ids})


def downgrade() -> None:
    pass
