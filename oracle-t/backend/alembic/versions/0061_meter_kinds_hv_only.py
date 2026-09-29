"""Пересчёт `tenders.meter_kinds` после правки правил для ВПУ (29.09.2026).

«Высоковольтный прибор учёта непосредственного включения» получал, кроме ВПУ, ещё шесть
видов обычных счётчиков прямого включения (закупка 32616409293): «непосредственного
включения» читалось как признак обычного счётчика. Теперь текст про ВПУ даёт только ВПУ
(`app/services/meter_kind.py`), а сохранённые типы закупок пересчитываются заново — иначе
фильтр списка по типу продолжал бы показывать такие закупки среди однофазных.

Заодно тип определяется только у закупок, где упомянут прибор учёта или серия счётчика:
«400 В, 3 фазы» и «номинальное напряжение 6 кВ» у кабельных проходов, ремонта отопления
или устройств плавного пуска — характеристики сети, а не счётчика (АП123076 получал все
одиннадцать типов). На базе 29.09 это 9 закупок из 424 с типом, все не про счётчики.

Revision ID: 0061_meter_kinds_hv_only
Revises: 0060_disabled_models_twins
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_meter_kinds_hv_only"
down_revision: Union[str, None] = "0060_disabled_models_twins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH = 2000


def upgrade() -> None:
    from app.services.meter_kind import tender_kinds

    bind = op.get_bind()
    requirements: dict[str, list[str]] = {}
    for tender_id, text, normalized in bind.execute(
        sa.text(
            "SELECT tender_id, text, normalized_text FROM requirements WHERE kind = 'product'"
        )
    ):
        requirements.setdefault(str(tender_id), []).append(f"{text or ''} {normalized or ''}")

    # Обновляются только изменившиеся: правка касается закупок с ВПУ, остальные те же.
    updates = []
    for tender_id, title, current in bind.execute(
        sa.text("SELECT id, title, meter_kinds FROM tenders")
    ):
        kinds = tender_kinds(title, requirements.get(str(tender_id)))
        if kinds != (list(current) if current else None):
            updates.append({"id": tender_id, "kinds": kinds})
    statement = sa.text("UPDATE tenders SET meter_kinds = :kinds WHERE id = :id").bindparams(
        sa.bindparam("kinds", type_=postgresql.ARRAY(sa.String(30)))
    )
    for start in range(0, len(updates), BATCH):
        bind.execute(statement, updates[start : start + BATCH])


def downgrade() -> None:
    # Прежние типы были ошибочными — возвращать их незачем.
    pass
