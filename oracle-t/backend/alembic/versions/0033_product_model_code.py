"""products.model_code — обозначение модели отдельно от её полного наименования

Наименование в справочнике должно совпадать с тем, что написано у производителя: в закупке
пишут «Счётчик электрической энергии однофазный интеллектуальный НАРТИС-Р1-М», а не
«НАРТИС-Р1-М», и человек, открывший каталог, должен видеть то же, что на сайте. Раньше
адаптер вычленял из названия код модели и сохранял в `model_name` именно его — полное
название терялось.

Но код всё равно нужен, и для другого: по нему модель сопоставляется с обозначением типа
в Госреестре (`si_type_linking`). Внутри полного наименования обозначение не находится
префиксным сравнением — «счетчикэлектроэнергииоднофазныйce101» не начинается с «ce101».

Поэтому две колонки вместо одной: `model_name` — как у производителя, `model_code` —
обозначение для сопоставления. Колонка nullable: у записей ручного ввода и CSV-импорта кода
нет, для них ключом остаётся само наименование.

Revision ID: 0033_product_model_code
Revises: 0032_competitor_catalog_sources
Create Date: 2026-09-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_product_model_code"
down_revision: Union[str, None] = "0032_competitor_catalog_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("model_code", sa.String(length=100), nullable=True))
    # Уже собранные записи каталога хранят в `model_name` именно код модели (адаптер работал
    # по старому правилу) — переносим его в новую колонку, чтобы привязка к типам СИ
    # продолжила работать до следующего обхода, который заполнит полные наименования.
    op.execute("UPDATE products SET model_code = model_name WHERE source_url IS NOT NULL")


def downgrade() -> None:
    op.drop_column("products", "model_code")
