"""Схемы записей о допуске продукции в реестрах (ПП 719, ЗАК Россетей, реестр ПО) —
замечание тестировщика 16.09.2026."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class RegistryInfoOut(BaseModel):
    key: str
    label: str
    official_name: str
    url: str | None
    # Упомянут ли реестр в требованиях закупки (заполняется в сводке по тендеру).
    mentioned: bool = False


class RegistryRecordOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    registry: str
    presence: str
    record_number: str | None
    issued_at: date | None
    valid_to: date | None
    url: str | None
    note: str | None
    verified_by_user: bool
    verified_at: datetime | None
    # Состояние, посчитанное по датам на сегодня: active / expiring / expired / absent.
    state: str
    summary: str
    updated_at: datetime


class RegistryRecordUpsert(BaseModel):
    presence: str = "present"
    record_number: str | None = Field(default=None, max_length=100)
    issued_at: date | None = None
    valid_to: date | None = None
    url: str | None = None
    note: str | None = None
    verified: bool = False


class ProductRegistryRowOut(BaseModel):
    """Строка карточки модели: реестр и запись по нему, если заведена."""

    registry: str
    label: str
    official_name: str
    url: str | None
    state: str
    record: RegistryRecordOut | None


class RegistryProductStateOut(BaseModel):
    product_id: uuid.UUID
    model_name: str
    state: str
    summary: str
    record_number: str | None
    valid_to: date | None
    url: str | None


class RegistryManufacturerStateOut(BaseModel):
    state: str
    products: list[RegistryProductStateOut]


class TenderRegistryRequirementOut(BaseModel):
    id: uuid.UUID
    text: str
    criticality: str | None
    registries: list[str]


class TenderRegistryManufacturerOut(BaseModel):
    manufacturer_id: uuid.UUID
    name: str
    is_mirtek: bool
    registries: dict[str, RegistryManufacturerStateOut]


class TenderRegistryCheckOut(BaseModel):
    requirements: list[TenderRegistryRequirementOut]
    mentioned_registries: list[str]
    registries: list[RegistryInfoOut]
    manufacturers: list[TenderRegistryManufacturerOut]
