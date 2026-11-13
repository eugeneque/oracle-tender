"""Типы СИ с чужой маркой у производителя, найденные только по марке (раздел 107 ARCHITECTURE.md).

Замечание 12.11.2026: у РиМ (АО «Радио и Микроэлектроника») в каталоге стояли МИРТЕК-12-РУ-SP17,
-D17, -W9, МИРТЕК-32-РУ-SP37, -D37. В ФГИС их изготовитель — контрактный завод ООО «РИМ-РУС»,
и поиск по марке «РиМ» признал типы 61891-15 и 65634-16 своими (`matched_by = 'brand'`).
Новый поиск такие типы отбрасывает (`si_type_linking.drop_foreign_brand_results`), а уже
заведённые убирает эта миграция:

* тип СИ, найденный только по марке (не подтверждённый человеком, не ручной), в обозначении
  или названии которого стоит марка другого производителя справочника, удаляется;
* модели этого производителя, заведённые из реестра ФГИС под таким типом, удаляются вместе
  с характеристиками; ссылки на них в матрице соответствия обнуляются (вердикт остаётся);
* модели с сайта производителя под таким типом только отвязываются от него.

Revision ID: 0106_drop_foreign_brand_types
Revises: 0105_yandex_price_fix
Create Date: 2026-11-12
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0106_drop_foreign_brand_types"
down_revision: Union[str, None] = "0105_yandex_price_fix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _words(brand: str | None) -> set[str]:
    return {
        word.upper().replace("Ё", "Е")
        for word in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", brand or "")
        if len(word) >= 4
    }


def upgrade() -> None:
    bind = op.get_bind()
    brands = {row.id: _words(row.brand_name) for row in bind.execute(sa.text("SELECT id, brand_name FROM manufacturers"))}
    suspects = bind.execute(
        sa.text(
            """
            SELECT id, manufacturer_id, notation, type_name
              FROM si_types
             WHERE matched_by = 'brand' AND NOT verified_by_user AND source <> 'manual'
            """
        )
    ).fetchall()
    for si_type in suspects:
        text = f"{si_type.notation or ''} {si_type.type_name or ''}".upper().replace("Ё", "Е")
        own = brands.get(si_type.manufacturer_id, set())
        foreign = any(
            re.search(rf"(?<![0-9A-ZА-Я]){re.escape(word)}(?![0-9A-ZА-Я])", text)
            for manufacturer_id, words in brands.items()
            if manufacturer_id != si_type.manufacturer_id
            for word in words - own
        )
        if not foreign:
            continue
        params = {"si_type_id": si_type.id}
        fgis_products = "SELECT id FROM products WHERE si_type_id = :si_type_id AND data_source = 'fgis'"
        bind.execute(sa.text(f"UPDATE compliance_matrix_entries SET product_id = NULL WHERE product_id IN ({fgis_products})"), params)
        bind.execute(sa.text(f"DELETE FROM product_characteristics WHERE product_id IN ({fgis_products})"), params)
        bind.execute(sa.text("DELETE FROM products WHERE si_type_id = :si_type_id AND data_source = 'fgis'"), params)
        bind.execute(sa.text("UPDATE products SET si_type_id = NULL WHERE si_type_id = :si_type_id"), params)
        bind.execute(sa.text("DELETE FROM si_types WHERE id = :si_type_id"), params)


def downgrade() -> None:
    # Удалённые записи — ошибка сопоставления; восстанавливать их нечего.
    pass
