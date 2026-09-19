"""Доля рынка производителя и дополнительные производители (замечания тестировщика 18.09.2026).

Список производителей в справочнике показывался по алфавиту; человеку, который сравнивает
конкурентов, важнее видеть их по весу на рынке. Доля хранится в записи производителя вместе
с источником оценки: цифры берутся из внешней аналитики (сейчас — Onside за 2024 год), и без
подписи «откуда» они через год станут неотличимы от выдумки. У кого доля не опубликована —
`NULL`, такие идут в конце списка по алфавиту.

Заодно заводятся четыре производителя, не попавшие в раздел 4.3 ТЗ (Ленэлектро, Матрица,
ТехноЭнерго, Эльстер Метроника), и заполняется сайт Инкотекса, в ТЗ отсутствовавший.

Revision ID: 0048_manufacturer_market_share
Revises: 0047_ai_decision
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.seed.manufacturers_data import (
    MANUFACTURERS_ADDED_2026_09,
    MARKET_SHARE_SOURCE,
    MARKET_SHARES_2024,
)

revision: str = "0048_manufacturer_market_share"
down_revision: Union[str, None] = "0047_ai_decision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("manufacturers", sa.Column("market_share_pct", sa.Numeric(5, 2), nullable=True))
    op.add_column("manufacturers", sa.Column("market_share_source", sa.Text(), nullable=True))

    conn = op.get_bind()
    for legal_name, brand_name, website, is_mirtek in MANUFACTURERS_ADDED_2026_09:
        exists = conn.execute(
            sa.text("SELECT 1 FROM manufacturers WHERE legal_name = :name"), {"name": legal_name}
        ).first()
        if exists:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO manufacturers (id, legal_name, brand_name, website, is_mirtek) "
                "VALUES (gen_random_uuid(), :name, :brand, :website, :is_mirtek)"
            ),
            {"name": legal_name, "brand": brand_name, "website": website, "is_mirtek": is_mirtek},
        )

    for legal_name, pct in MARKET_SHARES_2024.items():
        conn.execute(
            sa.text(
                "UPDATE manufacturers SET market_share_pct = :pct, market_share_source = :src "
                "WHERE legal_name = :name"
            ),
            {"pct": pct, "src": MARKET_SHARE_SOURCE, "name": legal_name},
        )

    conn.execute(
        sa.text(
            "UPDATE manufacturers SET website = 'https://www.incotexcom.ru/' "
            "WHERE legal_name = :name AND website IS NULL"
        ),
        {"name": 'ООО «НПК "Инкотекс"»'},
    )


def downgrade() -> None:
    conn = op.get_bind()
    names = [legal_name for legal_name, *_ in MANUFACTURERS_ADDED_2026_09]
    conn.execute(
        sa.text(
            "DELETE FROM manufacturers WHERE legal_name IN :names "
            "AND NOT EXISTS (SELECT 1 FROM products p WHERE p.manufacturer_id = manufacturers.id) "
            "AND NOT EXISTS (SELECT 1 FROM si_types s WHERE s.manufacturer_id = manufacturers.id)"
        ).bindparams(sa.bindparam("names", value=names, expanding=True))
    )
    op.drop_column("manufacturers", "market_share_source")
    op.drop_column("manufacturers", "market_share_pct")
