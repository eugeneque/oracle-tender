"""Третий ИИ-провайдер — DeepSeek через тот же RouterAI (28.09.2026).

Ключ и адрес шлюза общие с Claude, отличается только путь до модели — отдельное поле, чтобы
обе модели настраивались независимо (смена поколения одной не трогает другую). `NULL` —
модель по умолчанию из `app/services/ai_provider_service.py`.

Revision ID: 0056_deepseek_model
Revises: 0055_meter_parameters
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_deepseek_model"
down_revision: Union[str, None] = "0055_meter_parameters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_settings",
        sa.Column("deepseek_model", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    # Выбор DeepSeek после отката кода трактовался бы как Yandex (см. get_default_provider),
    # но у пользователей он остался бы висеть — сбрасываем явно.
    op.execute("UPDATE users SET ai_provider = NULL WHERE ai_provider = 'deepseek'")
    op.execute(
        "UPDATE ai_provider_settings SET active_provider = 'yandex' "
        "WHERE active_provider = 'deepseek'"
    )
    op.drop_column("ai_provider_settings", "deepseek_model")
