"""Пересведение написаний рынка ПУ после правки правил шаблонов (12.11.2026, раздел 102
ARCHITECTURE.md).

Схема не меняется — только данные. Шаблоны «НЕВА СП311 G/R» понимались как «G или R», а
одиночные буквы находились внутри слов («B» и «C» — в «BCS»): СП311 не размечался вовсе,
«НЕВА СТ221 … BCS-G3E» был привязан к «B». Правила исправлены в `meter_market_normalize`;
здесь один раз перепроверяются неразмеченные и автоматически привязанные написания
(`remap_auto(recheck=True)`; ручные — не трогаются). Сбой пересведения выкладку не роняет:
то же делает кнопка пересведения справочника.

Revision ID: 0099_meter_market_remap_letters
Revises: 0098_tender_protocol_results
Create Date: 2026-11-12
"""

import logging
from typing import Sequence, Union

from alembic import op
from sqlalchemy.orm import Session

revision: str = "0099_meter_market_remap_letters"
down_revision: Union[str, None] = "0098_tender_protocol_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    try:
        from app.services.meter_market_dictionary import remap_auto

        # Своя точка сохранения: сбой откатит только пересведение, не выкладку.
        session = Session(bind=op.get_bind(), join_transaction_mode="create_savepoint")
        try:
            changed = remap_auto(session, recheck=True)
            session.commit()  # снимает точку сохранения, внешнюю транзакцию фиксирует alembic
        except Exception:
            session.rollback()
            raise
        logger.info(f"Рынок ПУ: пересведено написаний — {changed}")
    except Exception as exc:  # noqa: BLE001 - данные, не схема: выкладку не роняем
        logger.warning(f"Рынок ПУ: пересведение написаний не выполнено: {exc}")


def downgrade() -> None:
    pass
