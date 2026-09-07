"""si_types: поля карточки утверждённого типа СИ из реестра ФГИС

Адаптер ФГИС переписан под реальный API реестра «Утверждённые типы СИ» (см. докстринг
app/adapters/fgis.py): вместо одной ссылки на документ оттуда теперь приходит карточка типа —
обозначение, наименование, МПИ, срок действия, признак актуальности и **версия** «Описания
типа». Версия здесь не украшение: у одного типа бывает несколько редакций документа
(у 61891-15 — четыре, по разным приказам Росстандарта), и без её хранения система молча
сверяла бы требования тендера с отменённой редакцией.

Все колонки nullable — уже найденные записи остаются валидными и дозаполняются при
следующем автопоиске, отдельного backfill не требуется.

Revision ID: 0015_si_type_registry_fields
Revises: 0014_product_catalog
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_si_type_registry_fields"
down_revision: Union[str, None] = "0014_product_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    sa.Column("notation", sa.String(length=500), nullable=True),
    sa.Column("type_name", sa.String(length=500), nullable=True),
    sa.Column("mit_uuid", sa.String(length=64), nullable=True),
    sa.Column("description_type_mirror_url", sa.Text(), nullable=True),
    sa.Column("description_type_version", sa.String(length=20), nullable=True),
    sa.Column("mpi_months", sa.Integer(), nullable=True),
    sa.Column("valid_to", sa.Date(), nullable=True),
    sa.Column("is_actual", sa.Boolean(), nullable=True),
    sa.Column("matched_by", sa.String(length=20), nullable=True),
)


def upgrade() -> None:
    for column in _NEW_COLUMNS:
        op.add_column("si_types", column)

    # Карточка типа перезапрашивается по mit_uuid при каждом обновлении — индекс нужен для
    # обратного поиска записи по идентификатору ФГИС.
    op.create_index("ix_si_types_mit_uuid", "si_types", ["mit_uuid"])


def downgrade() -> None:
    op.drop_index("ix_si_types_mit_uuid", table_name="si_types")
    for column in reversed(_NEW_COLUMNS):
        op.drop_column("si_types", column.name)
