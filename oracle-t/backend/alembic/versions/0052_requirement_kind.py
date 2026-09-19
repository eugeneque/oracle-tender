"""Вид требования: к товару, к услугам/работам, к участнику (замечание 18.09.2026).

Анализ документов извлекал только требования к прибору, а требования к работам и услугам
велел пропускать. На закупке «Обслуживание систем АИИС КУЭ» (Фабрикант 3214995) техническое
задание на 27 тысяч знаков дало ноль требований — не сбой, а слепое пятно: компания по
своему профилю участвует и в обслуживании, монтаже, поверке, а анализ был заточен под
поставку. Теперь извлекаются все три вида, а вид хранится в требовании: матрица
соответствия строится только по требованиям к товару (сравнивать модель счётчика с
«персонал с IV группой допуска» бессмысленно), AI-оценка читает все.

Уже извлечённые требования — все к товару: иных до этой правки не извлекалось.

Revision ID: 0052_requirement_kind
Revises: 0051_rusprofile_integration
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_requirement_kind"
down_revision: Union[str, None] = "0051_rusprofile_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="product"),
    )


def downgrade() -> None:
    op.drop_column("requirements", "kind")
