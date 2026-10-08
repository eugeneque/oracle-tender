"""Убрать канал «Селдон» (08.10.2026).

Селдон заводился 30.09.2026 (миграция 0062) в расчёте на Seldon.API, но доступ по договору так
и не открыли: адаптер остался заглушкой, источник — «ожидает доступа», канал на странице
тендеров — пустым. Заказчик попросил убрать его совсем, чтобы пустой режим «Селдон» и красная
точка недоступности в «Настройках» не путали людей при разборе сбоев площадок.

Удаляется строка источника (его учётки уходят каскадом) и ключ `seldon` из площадок профилей
отбора. Закупок у Селдона быть не может — сбор ни разу не работал; если они всё же есть
(ручной импорт), строка не удаляется, а выключается, чтобы не потерять данные.

Откат возвращает строку источника в том виде, в каком её заводила 0062.

Revision ID: 0072_remove_seldon
Revises: 0071_gigachat_provider
Create Date: 2026-10-08
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0072_remove_seldon"
down_revision: Union[str, None] = "0071_gigachat_provider"
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

SELDON_KEY = "seldon"


def upgrade() -> None:
    has_tenders = sa.exists().where(tenders.c.source_id == sources.c.id)
    op.execute(sources.delete().where(sources.c.key == SELDON_KEY, sa.not_(has_tenders)))
    # Осталась — значит, на неё ссылаются закупки: выключаем, а не удаляем.
    op.execute(
        sources.update()
        .where(sources.c.key == SELDON_KEY)
        .values(status="disabled", adapter_key=None, adapter_status="not_implemented")
    )
    op.execute(
        sa.text(
            "UPDATE relevance_profiles SET source_keys = array_remove(source_keys, CAST(:key AS varchar)) "
            "WHERE CAST(:key AS varchar) = ANY(source_keys)"
        ).bindparams(key=SELDON_KEY)
    )


def downgrade() -> None:
    exists = op.get_bind().scalar(
        sa.select(sa.func.count()).select_from(sources).where(sources.c.key == SELDON_KEY)
    )
    if exists:
        return
    op.execute(
        sources.insert().values(
            id=uuid.uuid4(),
            key=SELDON_KEY,
            name="Селдон",
            url="https://tender.myseldon.com/",
            type="seldon",
            status="pending_access",
            polling_schedule="twice_daily",
            adapter_key="seldon",
            adapter_status="not_implemented",
            note="Seldon.API — выгрузка закупок из системы Seldon. Доступ, документация и ключ"
            " выдаются по договору (8-800-2000-100).",
        )
    )
