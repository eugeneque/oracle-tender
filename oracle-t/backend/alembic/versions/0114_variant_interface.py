"""«Рынок ПУ»: интерфейс по коду в написании, таблица кодов типа, согласование спорных (раздел 122 ARCHITECTURE.md).

Аналитик (23.11.2026, Excel по «Заводу НАРТИС»): у написаний без кода интерфейса в отчёте стоял интерфейс
модификации («RF и RS»), у «…-ST-G/5-P1-…» — тоже «RF и RS» вместо NB-IoT. Интерфейс хранился только на
модификации, а сведение кладёт в одну модификацию написания с разными кодами.

- `meter_market_types.interface_codes` — таблица кодов интерфейсов типа (текст, правит аналитик);
  НАРТИС-И100 (86199-22) и НАРТИС-И300 (86200-22) получают таблицу каналов аналитика.
- `meter_market_variants.interface*` — интерфейс написания (`rule`/`manual`), примечание, предложение Совы
  и причина согласования. Заполняет `meter_market_interface.apply_interface_codes`.

Revision ID: 0114_variant_interface
Revises: 0113_mirtek_answers_23_11
Create Date: 2026-11-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0114_variant_interface"
down_revision: Union[str, None] = "0113_mirtek_answers_23_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VARIANT_COLUMNS = (
    ("interface", sa.String(length=255)),
    ("interface_source", sa.String(length=10)),
    ("interface_note", sa.Text()),
    ("interface_suggested", sa.String(length=255)),
    ("interface_review", sa.Text()),
    ("interface_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.add_column("meter_market_types", sa.Column("interface_codes", sa.Text(), nullable=True))
    for name, column_type in _VARIANT_COLUMNS:
        op.add_column("meter_market_variants", sa.Column(name, column_type, nullable=True))
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM meter_market_types")).scalar():
        from app.services.meter_market_interface import apply_interface_codes, apply_nartis_codes

        apply_nartis_codes(conn)
        apply_interface_codes(conn)


def downgrade() -> None:
    for name, _ in reversed(_VARIANT_COLUMNS):
        op.drop_column("meter_market_variants", name)
    op.drop_column("meter_market_types", "interface_codes")
