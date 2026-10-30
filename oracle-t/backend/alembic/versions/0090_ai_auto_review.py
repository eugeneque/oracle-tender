"""Автоматический разбор закупок через YandexGPT (раздел 81 ARCHITECTURE.md).

Яндекс временно отдаёт свои модели бесплатно, и разбирать закупку только по кнопке «Разбор ИИ»
в открытой карточке — значит оставлять неразобранными те, до которых человек не дошёл. Флаг
`auto_review_enabled` включает фоновую очередь: открытые релевантные закупки без заключения
встают на полный разбор по одной, ближайший срок подачи — первым. Флаг — в синглтоне
настроек моделей, потому что включает и выключает его администратор там же, на «Модели ИИ».

Revision ID: 0090_ai_auto_review
Revises: 0089_meter_market_rpp_records
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0090_ai_auto_review"
down_revision: Union[str, None] = "0089_meter_market_rpp_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_settings",
        sa.Column(
            "auto_review_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_provider_settings", "auto_review_enabled")
