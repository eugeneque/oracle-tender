"""Стоимость YandexGPT по тарифу из счёта (раздел 104 ARCHITECTURE.md).

Миграция 0101 проставила записям Yandex с 30.10.2026 цену 0,40 ₽ за 1000 токенов — по
публичным новостям о YandexGPT 5.1 Pro. Счёт Yandex Cloud за 14.10–12.11.2026 показал другое:
75,5 млн токенов 30.10 — ~90 тыс. ₽, 10,9 млн 06.11 — ~13 тыс. ₽, то есть 1,20 ₽ за 1000, а
30.10 ещё покрыт скидкой. Пересчёт: по 30.10 включительно — ноль, с 31.10 — 1,20 ₽.

Revision ID: 0105_yandex_price_fix
Revises: 0104_tender_price_vat
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0105_yandex_price_fix"
down_revision: Union[str, None] = "0104_tender_price_vat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE ai_usage_records
           SET cost = CASE
                 WHEN created_at < timestamptz '2026-10-31 00:00:00+03' THEN 0
                 ELSE round((coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0)) * 1.20 / 1000, 6)
               END
         WHERE provider = 'yandex'
           AND tokens_estimated = false
           AND created_at >= timestamptz '2026-10-30 00:00:00+03'
        """
    )


def downgrade() -> None:
    pass
