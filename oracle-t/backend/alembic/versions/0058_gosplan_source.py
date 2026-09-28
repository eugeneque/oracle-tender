"""Источник «Госплан» — API данных ЕИС отдельным каналом сбора (решение 28.09.2026).

На странице тендеров появился переключатель «Стандартные ресурсы | Госплан»: первый
показывает закупки с площадок из «Настройки → Источники тендеров», второй — собранные через
API Госплана. Строка источника заводится здесь; адаптер — `app/adapters/gosplan.py`.

Откат удаляет строку только если на неё не ссылается ни одна закупка — как в 0041.

Revision ID: 0058_gosplan_source
Revises: 0057_ai_conclusion_feedback
Create Date: 2026-09-28
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from app.seed.sources_data import GOSPLAN_SOURCE

revision: str = "0058_gosplan_source"
down_revision: Union[str, None] = "0057_ai_conclusion_feedback"
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
    key, name, url, type_, adapter_key, adapter_status, schedule, note = GOSPLAN_SOURCE
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
        sources.delete().where(sources.c.key == GOSPLAN_SOURCE[0], sa.not_(has_tenders))
    )
