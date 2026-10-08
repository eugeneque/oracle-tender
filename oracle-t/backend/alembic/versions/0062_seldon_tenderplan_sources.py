"""Источники «Селдон» и «Тендерплан» — отдельные каналы сбора, как Госплан (30.09.2026).

На переключателе страницы тендеров к «Стандартные ресурсы | Госплан» добавились «Селдон» и
«Тендерплан». Строки источников заводятся здесь; адаптеры — `app/adapters/seldon.py` и
`app/adapters/tenderplan.py`.

Откат удаляет строку только если на неё не ссылается ни одна закупка — как в 0058.

Revision ID: 0062_seldon_tenderplan_sources
Revises: 0061_meter_kinds_hv_only
Create Date: 2026-09-30
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.seed.sources_data import TENDERPLAN_SOURCE

# Строка Селдона — копией здесь, а не импортом: из `sources_data` она убрана вместе с каналом
# (08.10.2026, миграция 0072), а история миграций должна накатываться на чистую базу как раньше.
SELDON_SOURCE: tuple[str, str, str, str, str | None, str, str, str | None] = (
    "seldon",
    "Селдон",
    "https://tender.myseldon.com/",
    "seldon",
    "seldon",
    "not_implemented",
    "twice_daily",
    "Seldon.API — выгрузка закупок из системы Seldon. Доступ, документация и ключ выдаются"
    " по договору (8-800-2000-100); до этого канал пуст, сбор сообщает, чего не хватает.",
)

revision: str = "0062_seldon_tenderplan_sources"
down_revision: Union[str, None] = "0061_meter_kinds_hv_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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
    sa.column("note", sa.Text),
)

tenders = sa.table("tenders", sa.column("source_id", UUID(as_uuid=True)))

_SEEDED = (SELDON_SOURCE, TENDERPLAN_SOURCE)
# Селдон — «ожидает доступа»: плановый опрос его пропускает, пока нет Seldon.API, а кнопка
# сбора в настройках объясняет, чего не хватает.
_STATUS = {SELDON_SOURCE[0]: "pending_access"}


def upgrade() -> None:
    for key, name, url, type_, adapter_key, adapter_status, schedule, note in _SEEDED:
        op.execute(
            sources.insert().values(
                id=uuid.uuid4(),
                key=key,
                name=name,
                url=url,
                type=type_,
                status=_STATUS.get(key, "active"),
                polling_schedule=schedule,
                adapter_key=adapter_key,
                adapter_status=adapter_status,
                note=note,
            )
        )


def downgrade() -> None:
    has_tenders = sa.exists().where(tenders.c.source_id == sources.c.id)
    for seeded in _SEEDED:
        op.execute(sources.delete().where(sources.c.key == seeded[0], sa.not_(has_tenders)))
