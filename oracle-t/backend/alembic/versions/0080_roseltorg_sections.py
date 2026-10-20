"""Корпоративные секции Росэлторга — отдельные источники (20.10.2026).

Зачем. МИРТЕК работает на секциях Россетей, РусГидро, Ростелекома и Интер РАО и попросил
завести их площадками. До этого их закупки попадали только через общую витрину
`roseltorg`, да и то редко: адаптер читал одну страницу выдачи, которую почти целиком
занимал Росатом. Строки источников — из `ROSELTORG_SECTION_SOURCES`, адаптеры — секционные
классы в `app/adapters/roseltorg.py`.

Уже собранные общей витриной закупки секций с собственными номерами площадки
(`ROSSETI…`, `RH…`, `RT…`) переносятся к своей секции. Закупки с номером ЕИС по номеру
секцию не выдают — их секция заберёт себе при первом же опросе
(`tender_service._upsert_tender`).

Откат возвращает перенесённые закупки общей витрине и удаляет строки секций.
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.seed.sources_data import ROSELTORG_SECTION_SOURCES

revision: str = "0080_roseltorg_sections"
down_revision: Union[str, None] = "0079_qwen_provider"
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

# Префикс номера площадки → секция. Регулярное выражение Postgres (`~`).
_PREFIXES = {
    "roseltorg_rosseti": "^ROSSETI[0-9]",
    "roseltorg_rushydro": "^RH[0-9]",
    "roseltorg_rt": "^RT[0-9]",
}

_MOVE_SQL = sa.text(
    """
    UPDATE tenders t SET source_id = dst.id
    FROM sources src, sources dst
    WHERE src.key = :src AND dst.key = :dst AND t.source_id = src.id
      AND t.external_id ~ :pattern
      AND NOT EXISTS (
          SELECT 1 FROM tenders d WHERE d.source_id = dst.id AND d.external_id = t.external_id
      )
    """
)


def upgrade() -> None:
    for key, name, url, type_, adapter_key, adapter_status, schedule, note in (
        ROSELTORG_SECTION_SOURCES
    ):
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
    bind = op.get_bind()
    for section_key, pattern in _PREFIXES.items():
        bind.execute(_MOVE_SQL, {"src": "roseltorg", "dst": section_key, "pattern": pattern})


def downgrade() -> None:
    bind = op.get_bind()
    for section in ROSELTORG_SECTION_SOURCES:
        bind.execute(_MOVE_SQL, {"src": section[0], "dst": "roseltorg", "pattern": "."})
        op.execute(sources.delete().where(sources.c.key == section[0]))
