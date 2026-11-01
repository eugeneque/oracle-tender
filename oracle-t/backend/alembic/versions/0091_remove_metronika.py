"""Метроника убрана из производителей (решение заказчика 01.11.2026).

13.10.2026 её оставили (раздел 51 ARCHITECTURE.md), теперь заказчик попросил убрать: в
сопоставлении и в списке производителей она лишняя. Удаляются производитель и его каталог —
типы СИ, модели, характеристики, строки матрицы соответствия и шансы на победу; таблицы
с `ondelete=CASCADE` (документы, очередь каталога, профили участия, оценки вердиктов)
чистятся базой. Ищем по названию, а не по id: id у каждой базы свой (вставлялась
миграцией 0048 или пришла из снимка). На свежей базе 0048 её уже не вставляет — строка
убрана из `MANUFACTURERS_ADDED_2026_09`, а эта миграция ничего не находит.

Рынок ПУ по поверкам Аршина («Аналитика → Рынок ПУ») — свой справочник
(`meter_market_manufacturers`), там Метроника остаётся: это данные о рынке, а не каталог.

Revision ID: 0091_remove_metronika
Revises: 0090_ai_auto_review
Create Date: 2026-11-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0091_remove_metronika"
down_revision: Union[str, None] = "0090_ai_auto_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TARGET = (
    "SELECT id FROM manufacturers "
    "WHERE legal_name ILIKE '%метроник%' OR brand_name ILIKE '%метроник%'"
)
_PRODUCTS = f"SELECT id FROM products WHERE manufacturer_id IN ({_TARGET})"
_SI_TYPES = f"SELECT id FROM si_types WHERE manufacturer_id IN ({_TARGET})"


def upgrade() -> None:
    op.execute(
        f"DELETE FROM compliance_matrix_entries WHERE manufacturer_id IN ({_TARGET}) "
        f"OR product_id IN ({_PRODUCTS})"
    )
    op.execute(f"DELETE FROM win_percentages WHERE manufacturer_id IN ({_TARGET})")
    op.execute(f"DELETE FROM product_characteristics WHERE product_id IN ({_PRODUCTS})")
    op.execute(f"DELETE FROM products WHERE manufacturer_id IN ({_TARGET})")
    # Чужая модель, ошибочно привязанная к типу Метроники, не удаляется — только отвязывается.
    op.execute(f"UPDATE products SET si_type_id = NULL WHERE si_type_id IN ({_SI_TYPES})")
    op.execute(f"DELETE FROM si_types WHERE manufacturer_id IN ({_TARGET})")
    op.execute(f"DELETE FROM manufacturers WHERE id IN ({_TARGET})")


def downgrade() -> None:
    # Каталог Метроники не восстановить — возвращается только сам производитель; модели
    # заново соберёт обход сайта.
    op.execute(
        "INSERT INTO manufacturers (id, legal_name, brand_name, website, is_mirtek) "
        "SELECT gen_random_uuid(), 'ООО «Эльстер Метроника»', 'Метроника (АЛЬФА)', "
        "'https://www.izmerenie.ru/catalog/', false "
        "WHERE NOT EXISTS (SELECT 1 FROM manufacturers WHERE legal_name = 'ООО «Эльстер Метроника»')"
    )
