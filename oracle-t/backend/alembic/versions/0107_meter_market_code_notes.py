"""«Рынок ПУ»: расшифровка кодов обозначения от аналитика у описания типа (раздел 108 ARCHITECTURE.md).

Замечание аналитика 13.11.2026 (Тайпит): интерфейсы берутся из строки справочника, в которую
сведены несколько исполнений («НЕВА СТ221 G4/G3», «G3E/G4E» — «GSM и RS»), хотя G3 — модем NB-IoT.
В «Описании типа» сказано лишь «G — GSM/GPRS модем», а что модель модуля 3 — NB-IoT, знает
руководство по эксплуатации. Такие знания аналитик пишет в `meter_market_types.code_notes`; ИИ-разметка
(`meter_market_markup`) ставит их выше описания типа и расшифровывает по ним каждое написание.

Заполняется по документу аналитика «комментарии (на примере Тайпит)» для пяти типов НЕВА; у СТ4
вторая строка «с G3E — GSM и RS» в документе — опечатка, по аналогии с СТ221 это G4E.

Revision ID: 0107_meter_market_code_notes
Revises: 0106_drop_foreign_brand_types
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0107_meter_market_code_notes"
down_revision: Union[str, None] = "0106_drop_foreign_brand_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOTES = {
    "61544-15": (
        "AS, A — только активная энергия: ПП 890 — не соответствует; 2AR (2AR2S) — активная и реактивная "
        "в двух направлениях. E4 — RS (EIA 485); O — без интерфейса удалённого доступа; RF — RF; GSM — GSM; "
        "PL — PLC; WF — Wi-Fi."
    ),
    "73138-18": (
        "G3E — NB-IoT и RS; G4E — GSM и RS; C2E — RS, PLC и RF; C2 — PLC и RF. "
        "Буква E после кода модема — дополнительно RS (EIA 485)."
    ),
    "75447-19": "G3/R4 — NB-IoT и RF; R2/R4 — ZigBee.",
    "87229-22": (
        "G3 — NB-IoT; G3E — NB-IoT и RS; G4 — GSM и RS; G4E — GSM и RS; UG4 — RS и GSM (сменный модуль)."
    ),
}


def upgrade() -> None:
    op.add_column("meter_market_types", sa.Column("code_notes", sa.Text(), nullable=True))
    for mit_number, notes in NOTES.items():
        op.execute(
            sa.text("UPDATE meter_market_types SET code_notes = :n WHERE mit_number = :m AND code_notes IS NULL").bindparams(
                n=notes, m=mit_number
            )
        )


def downgrade() -> None:
    op.drop_column("meter_market_types", "code_notes")
