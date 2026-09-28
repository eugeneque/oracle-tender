"""Срок свидетельства об утверждении типа больше не хранится в `si_types.review_status`.

Интерфейс считает его на лету по `valid_to`/`is_actual` (`app/services/si_type_state.py`)
и показывает отдельной группой. Записанные ревалидацией пометки «истекло/истекает» снимаются,
иначе они продолжали бы показываться как «требует ручной проверки».

Заодно снимается пометка «не электросчётчик» с типов, которые теперь опознаются как
электросчётчики (шаблон «Приборы учета электроэнергии…», НАРТИС-И500 92281-24).

Откат ничего не восстанавливает: пометки пересчитываются из `valid_to`, данных не теряется.

Revision ID: 0059_si_type_expiry_not_review
Revises: 0058_gosplan_source
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.adapters.fgis_matching import DeviceKind, classify_si_type

revision: str = "0059_si_type_expiry_not_review"
down_revision: Union[str, None] = "0058_gosplan_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE si_types
        SET review_status = 'ok', review_reason = NULL
        WHERE review_status = 'needs_review'
          AND (review_reason LIKE 'Свидетельство об утверждении типа %'
               OR review_reason LIKE '%помечен в реестре как неактуальный%')
        """
    )

    bind = op.get_bind()
    off_scope = bind.execute(
        sa.text(
            "SELECT id, type_name, notation FROM si_types "
            "WHERE review_status = 'needs_review' "
            "AND review_reason LIKE '%Справочник продукции ограничен%'"
        )
    ).fetchall()
    for row in off_scope:
        if classify_si_type(row.type_name, row.notation) is DeviceKind.ELECTRICITY_METER:
            bind.execute(
                sa.text(
                    "UPDATE si_types SET review_status = 'ok', review_reason = NULL WHERE id = :id"
                ),
                {"id": row.id},
            )


def downgrade() -> None:
    pass
