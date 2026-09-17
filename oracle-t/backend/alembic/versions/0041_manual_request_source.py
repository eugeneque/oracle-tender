"""Системный источник ручных заявок (решение 15.09.2026).

Заказчик иногда присылает проект договора с характеристиками приборов напрямую, минуя
площадки. Такую закупку заводят в системе руками с приложенным файлом и запускают по ней
обычный ИИ-анализ. `tenders.source_id` обязателен, поэтому всем заявкам нужен один общий
источник — он и добавляется здесь. Опрос тендеров и проверка доступности его пропускают
(`POLL_EXCLUDED_SOURCE_TYPES` в app/models/source.py).

Откат удаляет строку только если на неё не ссылается ни одной заявки — иначе миграция молча
потеряла бы заведённые человеком закупки.

Revision ID: 0041_manual_request_source
Revises: 0040_registry_learning
Create Date: 2026-09-15
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.seed.sources_data import MANUAL_SOURCE

revision: str = "0041_manual_request_source"
down_revision: Union[str, None] = "0040_registry_learning"
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


def upgrade() -> None:
    key, name, url, type_, adapter_key, adapter_status, schedule, note = MANUAL_SOURCE
    op.execute(
        sources.insert().values(
            id=uuid.uuid4(),
            key=key,
            name=name,
            url=url,
            type=type_,
            status="active",
            polling_schedule=schedule,
            adapter_key=adapter_key,
            adapter_status=adapter_status,
            note=note,
        )
    )


def downgrade() -> None:
    has_tenders = sa.exists().where(tenders.c.source_id == sources.c.id)
    op.execute(
        sources.delete().where(sources.c.key == MANUAL_SOURCE[0], sa.not_(has_tenders))
    )
