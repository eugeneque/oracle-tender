"""Несколько компаний в разделе «Моя компания» (решение 07.09.2026).

Профиль перестаёт быть одной строкой на производителя МИРТЕК: компаний, от чьего имени
ведётся работа с закупками, у группы больше одной, и разложить участие по юрлицам без
отдельных карточек нельзя. Ограничения количества нет.

Что меняется в схеме:

* `manufacturer_id` становится необязательным — дополнительные компании не производители из
  справочника, и придумывать им запись там значило бы засорять справочник ради внешнего
  ключа. Уникальность привязки сохранена частичным индексом (только для заполненных);
* появляется `is_primary` — основная компания, та самая, что питает AI-оценку и синхронизацию
  истории участий. Частичный уникальный индекс не даёт завести вторую основную;
* появляется `created_at` — порядок строк в списке.

Существующий профиль (он же единственный) помечается основным: до этой миграции вся система
считала его «нашей компанией», и оставить систему без основного профиля значило бы молча
выключить измерения «Задача» и «Компетенции».

Revision ID: 0037_company_profiles_multiple
Revises: 0036_wave3_catalog_sources
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_company_profiles_multiple"
down_revision: Union[str, None] = "0036_wave3_catalog_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_profile",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "company_profile",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.alter_column("company_profile", "manufacturer_id", nullable=True)

    op.drop_constraint(
        "uq_company_profile_manufacturer", "company_profile", type_="unique"
    )
    op.create_index(
        "uq_company_profile_manufacturer",
        "company_profile",
        ["manufacturer_id"],
        unique=True,
        postgresql_where=sa.text("manufacturer_id IS NOT NULL"),
    )

    # Основной становится самая ранняя запись — до миграции она и была единственной.
    op.execute(
        """
        UPDATE company_profile
           SET is_primary = TRUE
         WHERE id = (SELECT id FROM company_profile ORDER BY updated_at LIMIT 1)
        """
    )
    op.create_index(
        "uq_company_profile_primary",
        "company_profile",
        ["is_primary"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    # Назад помещается только одна компания — лишние удаляются, иначе не встанет обратно ни
    # NOT NULL на производителе, ни уникальность привязки.
    op.execute("DELETE FROM company_profile WHERE is_primary IS NOT TRUE")
    op.drop_index("uq_company_profile_primary", table_name="company_profile")
    op.drop_index("uq_company_profile_manufacturer", table_name="company_profile")
    op.execute("DELETE FROM company_profile WHERE manufacturer_id IS NULL")
    op.alter_column("company_profile", "manufacturer_id", nullable=False)
    op.create_unique_constraint(
        "uq_company_profile_manufacturer", "company_profile", ["manufacturer_id"]
    )
    op.drop_column("company_profile", "created_at")
    op.drop_column("company_profile", "is_primary")
