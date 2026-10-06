"""Удаляет фиктивный тип СИ «12345-19» из справочника (06.10.2026).

Запись осталась от тестового прогона 28.08 против рабочей базы и уехала в снимок данных
(`data/seed_snapshot.zip`), а оттуда — на сервер. Номер выдуманный, обозначения и
наименования нет, но стоит отметка «подтверждено пользователем», и потому тип шёл первым
в карточку Энергомеры при каждом расчёте матрицы соответствия, вытесняя настоящие описания
типа.

Удаляется только сам тип, и только пустой (без обозначения и наименования) — если под этим
номером когда-нибудь окажется настоящая запись, миграция её не тронет. Модель CE308-C36,
привязанная к нему, остаётся в каталоге без привязки к типу.

Revision ID: 0066_drop_fake_si_type
Revises: 0065_unified_profiles
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0066_drop_fake_si_type"
down_revision: Union[str, None] = "0065_unified_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FAKE = """
    SELECT id FROM si_types
    WHERE si_code = '12345-19'
      AND coalesce(notation, '') = ''
      AND coalesce(type_name, '') = ''
"""


def upgrade() -> None:
    op.execute(f"UPDATE products SET si_type_id = NULL WHERE si_type_id IN ({_FAKE})")
    op.execute(f"DELETE FROM catalog_documents WHERE si_type_id IN ({_FAKE})")
    op.execute(f"DELETE FROM catalog_lookup_queue WHERE si_type_id IN ({_FAKE})")
    op.execute(f"DELETE FROM si_types WHERE id IN ({_FAKE})")


def downgrade() -> None:
    # Фиктивную запись не восстанавливаем.
    pass
