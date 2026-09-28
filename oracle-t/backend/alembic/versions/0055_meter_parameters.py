"""Параметры для приборов учёта и типы счётчиков (файл тендерного отдела, 25.09.2026).

* `requirements.parameter_no` — к какому из 39 параметров файла «Параметры для ПУ»
  относится требование (`app/seed/meter_parameters.py`);
* `win_percentages.verdict` — итог «проходит / с оговорками / не проходит / не хватает
  данных» по производителю, отдельно от процента;
* `tenders.meter_kinds` — типы закупаемых приборов из одиннадцати («расширенный
  нейминг» для фильтра списка). Заполняется сразу по наименованию и требованиям,
  которые уже извлечены: фильтр по пустому полю выглядел бы сломанным.

Revision ID: 0055_meter_parameters
Revises: 0054_ai_score_checklists
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_meter_parameters"
down_revision: Union[str, None] = "0054_ai_score_checklists"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH = 2000


def upgrade() -> None:
    op.add_column("requirements", sa.Column("parameter_no", sa.SmallInteger(), nullable=True))
    op.add_column("win_percentages", sa.Column("verdict", sa.String(20), nullable=True))
    op.add_column(
        "tenders",
        sa.Column("meter_kinds", postgresql.ARRAY(sa.String(30)), nullable=True),
    )
    op.create_index(
        "ix_tenders_meter_kinds", "tenders", ["meter_kinds"], postgresql_using="gin"
    )
    _backfill_meter_kinds()


def _backfill_meter_kinds() -> None:
    from app.services.meter_kind import tender_kinds

    bind = op.get_bind()
    requirements: dict[str, list[str]] = {}
    for tender_id, text, normalized in bind.execute(
        sa.text(
            "SELECT tender_id, text, normalized_text FROM requirements WHERE kind = 'product'"
        )
    ):
        requirements.setdefault(str(tender_id), []).append(f"{text or ''} {normalized or ''}")

    updates = []
    for tender_id, title in bind.execute(sa.text("SELECT id, title FROM tenders")):
        kinds = tender_kinds(title, requirements.get(str(tender_id)))
        if kinds:
            updates.append({"id": tender_id, "kinds": kinds})
    statement = sa.text("UPDATE tenders SET meter_kinds = :kinds WHERE id = :id").bindparams(
        sa.bindparam("kinds", type_=postgresql.ARRAY(sa.String(30)))
    )
    for start in range(0, len(updates), BATCH):
        bind.execute(statement, updates[start : start + BATCH])


def downgrade() -> None:
    op.drop_index("ix_tenders_meter_kinds", table_name="tenders")
    op.drop_column("tenders", "meter_kinds")
    op.drop_column("win_percentages", "verdict")
    op.drop_column("requirements", "parameter_no")
