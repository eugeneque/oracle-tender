"""Полезная нагрузка фоновой задачи и опрос площадок в фоне (баг 17.09.2026: «бесконечная
синхронизация»).

Кнопка «Синхронизировать» держала HTTP-запрос открытым на весь опрос девяти площадок — по
журналу это 20–25 минут. Любой перезапуск сервера (в разработке — на каждое сохранение
файла при `--reload`), таймаут прокси или обрыв соединения гасил запрос без ответа, и
интерфейс крутил «Синхронизация с источниками…» вечно. Теперь опрос — фоновая задача той
же очереди, что и ИИ-анализ: ответ приходит сразу, интерфейс опрашивает состояние.

Задаче нужно знать, какие площадки опрашивать, — поле `payload` (JSONB). Оно же хранит
итог по каждой площадке.

Revision ID: 0046_job_payload
Revises: 0045_tags_and_account
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_job_payload"
down_revision: Union[str, None] = "0045_tags_and_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("background_jobs", "payload")
