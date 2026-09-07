"""Схемы истории участий (разделы 5.6, 7 ТЗ; источник заменён на реестр контрактов ЕИС
04.09.2026)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.company_participation import ParticipationOutcome, ParticipationSource


class ParticipationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tender_id: uuid.UUID | None
    external_tender_id: str | None
    tender_title: str | None
    customer_name: str | None
    customer_org_id: str | None
    our_inn: str | None
    our_bid: Decimal | None
    price_drop_pct: Decimal | None
    competitors_count: int | None
    outcome: str
    outcome_label: str
    final_contract_value: Decimal | None
    executed_at: date | None
    lessons_learned_md: str | None
    source: str
    source_label: str
    last_synced_at: datetime | None
    created_at: datetime


class ParticipationSummaryOut(BaseModel):
    """Сводка вкладки. `lost` и `disqualified` — раздельно: снятие с торгов по формальной
    причине и проигрыш по существу лечатся разными действиями (раздел 7 ТЗ)."""

    total: int
    won: int
    lost: int
    disqualified: int
    unknown: int
    # Доля побед среди участий с известным исходом; `null`, когда таких нет вовсе.
    win_rate: Decimal | None
    # Вся история пришла из источника, который знает только о победах (реестр контрактов
    # ЕИС). Интерфейс обязан сказать об этом рядом с win-rate: 100% по такой выборке —
    # свойство источника, а не факт о компании.
    wins_only_data: bool
    last_synced_at: datetime | None


class ParticipationListOut(BaseModel):
    items: list[ParticipationOut]
    summary: ParticipationSummaryOut


class ParticipationWrite(BaseModel):
    """Форма ручного добавления/правки записи (раздел 5.6 ТЗ: юрлицо, итог, ставка, цена
    контракта, дата, тендер, заметки)."""

    model_config = ConfigDict(extra="forbid")

    tender_id: uuid.UUID | None = None
    external_tender_id: str | None = Field(default=None, max_length=100)
    tender_title: str | None = None
    customer_name: str | None = Field(default=None, max_length=500)
    customer_org_id: str | None = Field(default=None, max_length=100)
    our_inn: str | None = Field(default=None, max_length=20)
    our_bid: Decimal | None = None
    price_drop_pct: Decimal | None = None
    competitors_count: int | None = Field(default=None, ge=0)
    outcome: str = ParticipationOutcome.UNKNOWN.value
    final_contract_value: Decimal | None = None
    executed_at: date | None = None
    lessons_learned_md: str | None = None


class ParticipationSyncResultOut(BaseModel):
    fetched: int
    created: int
    updated: int
    matched_tenders: int
    # Второй шаг выгрузки: проигрыши, выведенные из закупок с поданной заявкой.
    checked_submitted: int = 0
    losses_found: int = 0


OUTCOME_CHOICES = [item.value for item in ParticipationOutcome]
SOURCE_CHOICES = [item.value for item in ParticipationSource]
