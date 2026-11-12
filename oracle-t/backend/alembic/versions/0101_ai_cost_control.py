"""Контроль расходов на ИИ после конца бесплатного периода Яндекса (раздел 104 ARCHITECTURE.md).

Разбор выгрузки «Детализация ИИ» за 14.10–12.11.2026: с 30.10.2026 YandexGPT платная, а
«Автоматический разбор» (раздел 81) был закреплён за ней как за бесплатной — 30.10 он один
потратил 74 млн токенов. В детализации этого не было видно: SDK Yandex цену не возвращает.

* `auto_review_provider` — какой моделью идёт авторазбор (раньше — только Yandex). По умолчанию
  Qwen через RouterAI: по фактическим счетам она в разы дешевле YandexGPT Pro.
* `auto_review_daily_limit` — сколько закупок авторазбор берёт за московские сутки: один разбор —
  в среднем 1,3 млн токенов, и без потолка очередь из сотни закупок съела бы бюджет за день.
* Авторазбор выключается: он включался под бесплатный Яндекс, и включить его снова должен
  администратор, уже выбрав модель и лимит.
* `cost` у записей Yandex с 30.10.2026 дозаполняется по тарифу 0,40 ₽ за 1000 токенов (как
  `yandex_price_rub_per_1k` по умолчанию) — только у записей со счётчиком провайдера.

Revision ID: 0101_ai_cost_control
Revises: 0100_bitrix_tender_tracking
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0101_ai_cost_control"
down_revision: Union[str, None] = "0100_bitrix_tender_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_settings",
        sa.Column("auto_review_provider", sa.String(20), nullable=False, server_default="qwen"),
    )
    op.add_column(
        "ai_provider_settings",
        sa.Column("auto_review_daily_limit", sa.Integer(), nullable=False, server_default="10"),
    )
    op.execute("UPDATE ai_provider_settings SET auto_review_enabled = false")
    op.execute(
        """
        UPDATE ai_usage_records
           SET cost = round((coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0)) * 0.40 / 1000, 6)
         WHERE provider = 'yandex'
           AND cost IS NULL
           AND tokens_estimated = false
           AND created_at >= timestamptz '2026-10-30 00:00:00+03'
        """
    )


def downgrade() -> None:
    op.drop_column("ai_provider_settings", "auto_review_daily_limit")
    op.drop_column("ai_provider_settings", "auto_review_provider")
