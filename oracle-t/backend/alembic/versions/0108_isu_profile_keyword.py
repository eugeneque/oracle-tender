"""Слово «ИСУ» в общем профиле оборудования учёта (замечание 16.11.2026).

Тендер №32616555108 («Услуги по монтажу и наладке оборудования ИСУ…», Красноярскэнергосбыт,
135,8 млн ₽, приём заявок до 25.11) был собран, но ни один общий профиль его не пропустил:
в названии только аббревиатура «ИСУ» (интеллектуальная система учёта), без слов «счётчик» и
«прибор учёта». Слово добавляется профилю АСКУЭ/АИИС КУЭ/УСПД; точность дальше обеспечивает
ИИ-отбор. Правки администратора не затираются: слово дописывается к имеющимся.

Revision ID: 0108_isu_profile_keyword
Revises: 0107_meter_market_code_notes
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0108_isu_profile_keyword"
down_revision: Union[str, None] = "0107_meter_market_code_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WORD = "ису"


def _reset_marks() -> None:
    # Пересчитать нужно закупки, где «ису» есть в названии или у заказчика.
    op.execute(
        "UPDATE tenders SET passed_relevance_filter = NULL, matched_profile_id = NULL "
        "WHERE title ~* '(^|[^[:alnum:]])ису([^[:alnum:]]|$)' "
        "OR customer_name ~* '(^|[^[:alnum:]])ису([^[:alnum:]]|$)'"
    )
    op.execute("UPDATE relevance_profiles SET matched_at = NULL")


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE relevance_profiles
            SET keywords = keywords || to_jsonb(CAST(:w AS text)),
                rules_version = rules_version + 1
            WHERE is_default AND name LIKE 'Поставка оборудования учёта%'
              AND NOT (keywords @> jsonb_build_array(CAST(:w AS text)))
            """
        ).bindparams(w=_WORD)
    )
    _reset_marks()


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE relevance_profiles
            SET keywords = keywords - CAST(:w AS text),
                rules_version = rules_version + 1
            WHERE is_default AND name LIKE 'Поставка оборудования учёта%'
            """
        ).bindparams(w=_WORD)
    )
    _reset_marks()
