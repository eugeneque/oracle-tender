"""Порядок записей в журнале: clock_timestamp() вместо now() (раздел 5.9 ТЗ).

В PostgreSQL `now()` возвращает время НАЧАЛА транзакции. Опрос источника, разбор документа
или рассылка пишут в журнал несколько записей внутри одной транзакции — и все получали
одинаковую метку времени. В разделе «Логирование» такие строки выстраивались в произвольном
порядке, и по журналу нельзя было понять, что за чем шло. `clock_timestamp()` берёт реальный
момент вставки строки.

Revision ID: 0022_log_clock_timestamp
Revises: 0021_notifications
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0022_log_clock_timestamp"
down_revision: Union[str, None] = "0021_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    ("logs", "timestamp"),
    ("notifications", "created_at"),
    ("background_jobs", "created_at"),
)


def upgrade() -> None:
    for table, column in _TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT clock_timestamp()")


def downgrade() -> None:
    for table, column in _TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")
