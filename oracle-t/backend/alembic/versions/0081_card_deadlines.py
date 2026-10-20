"""Точные сроки подачи заявок у уже собранных закупок (20.10.2026, раздел 68 ARCHITECTURE.md).

Зачем. `tenders.application_end` был неточным: выдача ЕИС даёт окончание подачи только
датой (полночь), а Госплан по 223-ФЗ отдаёт местное время заказчика без пояса, которое
адаптер считал UTC. 32616389457: в извещении «29.09.2026 09:00 (МСК+4)», то есть 05:00 МСК,
а в базе — 12:00 МСК. Код исправлен (адаптер Госплана, уточнение по карточке ЕИС в
`tender_card_service.fill_gaps_from_card`), здесь — разовая правка собранного:

1. Сроки 223-ФЗ из Госплана сдвигаются на −3 часа: «настенное» время заказчика теперь
   считается московским, как в исправленном адаптере.
2. По сохранённым карточкам ЕИС (`tender_cards`) срок с временем ставится самой записи и
   записям той же закупки с других площадок, если их срок в тот же день (карточка могла
   устареть: 32616268723 — карточка от 01.09 со сроком 25.08 при продлении до 06.10); дата без
   времени — только в пустое поле. Логика та же, что в `eis_card_dates.refine_deadlines`,
   но на чистом SQL — миграция не должна зависеть от будущего состава ORM-моделей.

Откат возвращает сдвиг Госплана; уточнения по карточкам не откатываются — прежние значения
были ошибочными и не сохранялись.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.services.eis_card_dates import (
    DEADLINE_FIELDS,
    MSK,
    dates_from_payload,
    precise_deadlines,
    same_deadline,
)

revision: str = "0081_card_deadlines"
down_revision: Union[str, None] = "0080_roseltorg_sections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Номер закупки 223-ФЗ — 11 цифр, 44-ФЗ — 19 (у 44-ФЗ Госплан отдаёт настоящий UTC).
_GOSPLAN_223 = """
    UPDATE tenders SET application_end = application_end {sign} interval '3 hours'
    WHERE application_end IS NOT NULL
      AND length(external_id) = 11
      AND source_id IN (SELECT id FROM sources WHERE key = 'gosplan')
"""


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(_GOSPLAN_223.format(sign="-")))

    cards = conn.execute(
        sa.text(
            "SELECT c.tender_id, t.registry_number, c.payload FROM tender_cards c "
            "JOIN tenders t ON t.id = c.tender_id ORDER BY c.fetched_at"
        )
    ).all()
    for tender_id, registry_number, payload in cards:
        with_time = precise_deadlines(payload)
        date_only = {
            key: value
            for key, value in dates_from_payload(payload).items()
            if key in DEADLINE_FIELDS and key not in with_time
        }
        if not with_time and not date_only:
            continue
        records = conn.execute(
            sa.text(
                "SELECT id, application_start, application_end FROM tenders "
                "WHERE id = :id OR (:number IS NOT NULL AND registry_number = :number)"
            ),
            {"id": tender_id, "number": registry_number},
        ).all()
        for record_id, start, end in records:
            current = {"application_start": start, "application_end": end}
            updates = {}
            for key in DEADLINE_FIELDS:
                value = with_time.get(key)
                if value is not None:
                    # Сохранённая карточка могла устареть (срок потом продлили) — поэтому
                    # «тот же день» и для самой записи, не только для двойников.
                    if (
                        current[key] is not None
                        and not same_deadline(value, current[key].astimezone(MSK))
                    ):
                        continue
                    updates[key] = value.astimezone(MSK)
                elif current[key] is None and key in date_only:
                    updates[key] = date_only[key]
            if updates:
                assignments = ", ".join(f"{key} = :{key}" for key in updates)
                conn.execute(
                    sa.text(f"UPDATE tenders SET {assignments} WHERE id = :id"),
                    {**updates, "id": record_id},
                )


def downgrade() -> None:
    op.get_bind().execute(sa.text(_GOSPLAN_223.format(sign="+")))
