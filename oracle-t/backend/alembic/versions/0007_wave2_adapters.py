"""wave 2: roseltorg, etprf, etpgpb adapters implemented; astgoz and etpgpb_strateg
reclassified as access-restricted (blocked), not technical TLS/scraping issues

Revision ID: 0007_wave2_adapters
Revises: 0006_zakazrf_adapter_implemented
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_wave2_adapters"
down_revision: Union[str, None] = "0006_zakazrf_adapter_implemented"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sources = sa.table(
    "sources",
    sa.column("key", sa.String),
    sa.column("adapter_key", sa.String),
    sa.column("adapter_status", sa.String),
    sa.column("note", sa.String),
)

_IMPLEMENTED = ["roseltorg", "etprf", "etpgpb"]


def upgrade() -> None:
    for key in _IMPLEMENTED:
        op.execute(
            sources.update()
            .where(sources.c.key == key)
            .values(adapter_key=key, adapter_status="implemented", note=None)
        )

    op.execute(
        sources.update()
        .where(sources.c.key == "astgoz")
        .values(
            adapter_status="blocked",
            note="Площадка — «Автоматизированная система торгов государственного оборонного"
            " заказа»: публичного списка закупок без входа и электронной подписи (КриптоПро)"
            " нет — это легитимное ограничение доступа к гособоронзаказу, а не техническая"
            " проблема (TLS уже решён, см. resolve_verify). Подключение возможно только через"
            " официальную аккредитацию заказчика.",
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "etpgpb_strateg")
        .values(
            adapter_status="blocked",
            note="Раздел «Стратег» на ЭТП ГПБ — закрытые тендеры и торги по приглашениям,"
            " не имеет публичного списка (в отличие от основного раздела площадки, см. источник"
            " «ЭТП ГПБ» — там реализован адаптер через открытый JSON API). Подключение возможно"
            " только через официальную аккредитацию.",
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "lot_online")
        .values(
            note="В очереди на реализацию (Этап 12 ТЗ). При исследовании выяснилось, что"
            " площадка построена на Angular (`<app-root>`) — потребует Playwright."
        )
    )


def downgrade() -> None:
    for key in _IMPLEMENTED:
        op.execute(
            sources.update()
            .where(sources.c.key == key)
            .values(
                adapter_key=None,
                adapter_status="not_implemented",
                note="В очереди на реализацию (Этап 12 ТЗ).",
            )
        )
    op.execute(
        sources.update()
        .where(sources.c.key == "astgoz")
        .values(
            adapter_status="not_implemented",
            note="В очереди на реализацию (Этап 12 ТЗ). TLS-проблема решена (Russian Trusted CA,"
            " как для ЕИС) — площадка доступна.",
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "etpgpb_strateg")
        .values(adapter_status="not_implemented", note="В очереди на реализацию (Этап 12 ТЗ).")
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "lot_online")
        .values(note="В очереди на реализацию (Этап 12 ТЗ).")
    )
