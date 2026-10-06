"""Коды ОКПД2 электросчётчиков в общих профилях: .130, .131, .139 (замечание 06.10.2026).

Тендерный отдел попросил искать по 26.51.63.130, 26.51.63.131 (интеллектуальные приборы
учёта электроэнергии), 26.51.63.139 (прочие) и по группе 26.51.63. Отбор знал только .130 и
сравнивал код по началу строки, поэтому закупка с кодом .131 или .139 проходила только
словами, а закупка, где заказчик указал лишь вид 26.51.63, — тоже. Правило «вида»
добавлено в `okpd2_service.covers`; здесь общим профилям, у которых стоит .130, дописываются
две соседние категории. Правки администратора не затираются: коды добавляются к тем, что уже
есть.

Пересчитать нужно только закупки, чей код вообще может изменить решение, — с кодом из
ветки 26.51.63: их отметка сбрасывается в NULL, и `relevance_service.bootstrap` при старте
пересчитывает её. Совпадения личных профилей пересчитываются полностью при следующем
открытии списка (`matched_at` = NULL), потому что правило «вида» касается и их.

Revision ID: 0069_okpd2_electric_meters
Revises: 0068_relevance_mark
Create Date: 2026-10-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0069_okpd2_electric_meters"
down_revision: Union[str, None] = "0068_relevance_mark"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BASE = "26.51.63.130"
_ADDED = ("26.51.63.131", "26.51.63.139")


def _reset_marks() -> None:
    op.execute(
        "UPDATE tenders SET passed_relevance_filter = NULL, matched_profile_id = NULL "
        "WHERE okpd2_code LIKE '26.51.63%'"
    )
    op.execute("UPDATE relevance_profiles SET matched_at = NULL")


def upgrade() -> None:
    for code in _ADDED:
        op.execute(
            sa.text(
                """
                UPDATE relevance_profiles
                SET okpd2_codes = array_append(okpd2_codes, CAST(:code AS varchar(20))),
                    rules_version = rules_version + 1
                WHERE is_default AND :base = ANY(okpd2_codes) AND NOT (:code = ANY(okpd2_codes))
                """
            ).bindparams(code=code, base=_BASE)
        )
    _reset_marks()


def downgrade() -> None:
    for code in _ADDED:
        op.execute(
            sa.text(
                """
                UPDATE relevance_profiles
                SET okpd2_codes = array_remove(okpd2_codes, CAST(:code AS varchar(20))),
                    rules_version = rules_version + 1
                WHERE is_default AND :code = ANY(okpd2_codes)
                """
            ).bindparams(code=code)
        )
    _reset_marks()
