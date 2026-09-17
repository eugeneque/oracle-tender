"""Удаление трёх площадок, которые не удалось подключить (правка по итогам показа 15.09.2026).

РТС-тендер (антибот-защита, не проходит даже headless-браузер), АСТ ГОЗ (торги по
гособоронзаказу — публичного списка закупок без входа и ЭП нет) и ЭТП ГПБ «Стратег» (закрытые
торги по приглашениям, публичного списка нет) висели в разделе источников со статусом
«заблокировано» и путали конечного пользователя: площадка есть, а тендеров с неё нет и не
будет. Заказчик решил убрать их из системы вовсе.

Тендеров у этих источников быть не может — адаптеры для них никогда не существовали, — но
удаление всё равно ограничено строками без закупок: если на какой-то базе тендер к ним
привязан руками, миграция не должна молча его потерять. Учётные данные площадок (таблица
`source_credentials`) уходят каскадом по внешнему ключу.

Откат восстанавливает строки в том виде, в каком они были после миграций 0005 и 0007
(статус `blocked` с пояснением).

Revision ID: 0038_remove_blocked_sources
Revises: 0037_company_profiles_multiple
Create Date: 2026-09-15
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0038_remove_blocked_sources"
down_revision: Union[str, None] = "0037_company_profiles_multiple"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REMOVED_KEYS = ("rts_tender", "astgoz", "etpgpb_strateg")

sources = sa.table(
    "sources",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("key", sa.String),
    sa.column("name", sa.String),
    sa.column("url", sa.String),
    sa.column("type", sa.String),
    sa.column("status", sa.String),
    sa.column("polling_schedule", sa.String),
    sa.column("adapter_key", sa.String),
    sa.column("adapter_status", sa.String),
    sa.column("note", sa.String),
)

tenders = sa.table("tenders", sa.column("source_id", UUID(as_uuid=True)))

# (key, name, url, note) — для отката; статус адаптера у всех троих `blocked`.
_RESTORE = (
    (
        "rts_tender",
        "РТС-тендер",
        "https://www.rts-tender.ru/",
        "Площадка отдаёт страницу антибот-защиты (\"Anti-DDoS защита\") даже реальному"
        " headless-браузеру (Playwright, с ожиданием) — не только httpx. Дальнейший обход"
        " (подмена fingerprint и т.п.) не предпринимался. Подключение возможно только через"
        " официальную аккредитацию/API площадки.",
    ),
    (
        "astgoz",
        "АСТ ГОЗ",
        "https://www.astgoz.ru/page/index",
        "Площадка — «Автоматизированная система торгов государственного оборонного заказа»:"
        " публичного списка закупок без входа и электронной подписи (КриптоПро) нет — это"
        " легитимное ограничение доступа к гособоронзаказу, а не техническая проблема."
        " Подключение возможно только через официальную аккредитацию заказчика.",
    ),
    (
        "etpgpb_strateg",
        "ЭТП ГПБ (Стратег)",
        "https://etpgpb.ru/products/strateg/",
        "Раздел «Стратег» на ЭТП ГПБ — закрытые тендеры и торги по приглашениям, не имеет"
        " публичного списка (в отличие от основного раздела площадки, см. источник «ЭТП ГПБ»)."
        " Подключение возможно только через официальную аккредитацию.",
    ),
)


def upgrade() -> None:
    has_tenders = sa.exists().where(tenders.c.source_id == sources.c.id)
    op.execute(
        sources.delete().where(sources.c.key.in_(REMOVED_KEYS), sa.not_(has_tenders))
    )


def downgrade() -> None:
    for key, name, url, note in _RESTORE:
        op.execute(
            sources.insert().values(
                id=uuid.uuid4(),
                key=key,
                name=name,
                url=url,
                type="etp_federal_commercial",
                status="active",
                polling_schedule="twice_daily",
                adapter_key=None,
                adapter_status="blocked",
                note=note,
            )
        )
