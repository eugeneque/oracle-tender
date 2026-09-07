"""Схемы внешнего API (раздел 5.10 ТЗ).

Отдельно от схем интерфейса намеренно: формат, который читает чужая система, менять нельзя
так же свободно, как поля своей же страницы. Если завтра в карточке тендера появится новое
поле, внешний контракт останется прежним, пока его не поменяют осознанно.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class IntegrationTenderOut(BaseModel):
    """Тендер в плоском виде: коды регионов идут вместе со всем, что нужно для лида, а
    процент победителя уже посчитан — интеграции не нужно ходить в матрицу соответствия."""

    id: uuid.UUID
    external_id: str
    title: str
    source: str
    source_key: str
    source_url: str | None
    customer_name: str | None
    organizer_name: str | None
    procurement_method: str | None
    status: str | None
    relevance_status: str
    price: float | None
    currency: str
    publish_date: date | None
    application_start: datetime | None
    application_end: datetime | None
    okpd2_code: str | None
    tender_type: str | None
    region_organizer_code: str | None
    region_delivery_code: str | None
    federal_district_code: int | None
    ai_comment: str | None
    win_percentage: float | None
    created_at: datetime
    updated_at: datetime


class IntegrationRequirementOut(BaseModel):
    id: uuid.UUID
    text: str
    criticality: str
    category: str | None
    verified_by_user: bool


class IntegrationWinPercentageOut(BaseModel):
    manufacturer: str
    is_mirtek: bool
    percentage: float
    requirements_total: int
    requirements_scored: int


class IntegrationTenderDetailOut(IntegrationTenderOut):
    requirements: list[IntegrationRequirementOut]
    win_percentages: list[IntegrationWinPercentageOut]
    compliance_entries_count: int


class IntegrationTenderPage(BaseModel):
    items: list[IntegrationTenderOut]
    total: int
    limit: int
    offset: int


class IntegrationLeadField(BaseModel):
    key: str
    title: str


class IntegrationLeadPage(BaseModel):
    """Лиды и описание полей в одном ответе: интеграция сопоставляет колонки один раз, по
    `fields`, а не зашивает список полей у себя."""

    items: list[dict[str, Any]]
    fields: list[IntegrationLeadField]
    total: int


class ApiClientOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None


class ApiClientCreated(ApiClientOut):
    """Ответ на создание ключа — единственное место, где ключ виден целиком."""

    key: str


class ApiClientCreate(BaseModel):
    name: str
