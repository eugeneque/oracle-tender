"""Схемы ответов по ПО верхнего уровня (замечание тестировщика 16.09.2026)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class UpperSoftwarePlatformOut(BaseModel):
    """Площадка со состоянием её списка поддерживаемого оборудования."""

    adapter_key: str
    name: str
    vendor: str
    url: str
    source_id: uuid.UUID | None
    devices_total: int
    # Сколько записей списка отнесено к производителям из нашего справочника.
    devices_matched: int
    last_synced_at: datetime | None
    availability_status: str | None


class UpperSoftwareSyncOutcomeOut(BaseModel):
    adapter_key: str
    devices_read: int
    created: int
    updated: int
    disappeared: int
    with_manufacturer: int
    products_linked: int
    errors: list[str]


class UpperSoftwareDeviceOut(BaseModel):
    """Запись списка как на сайте плюс результат разбора и связанные модели."""

    id: uuid.UUID
    section: str
    device_raw: str
    device_names: list[str]
    manufacturer_raw: str | None
    si_codes: list[str]
    is_generic: bool
    details: dict
    manufacturer_matched_by: str | None
    products: list[str]


class PlatformSupportOut(BaseModel):
    """Статус производителя на одной площадке: `supported`, `not_listed`, `protocol_only`,
    `not_synced` (см. `PlatformSupport` в сервисе)."""

    key: str
    name: str
    vendor: str
    url: str
    status: str
    last_synced_at: datetime | None
    devices: list[UpperSoftwareDeviceOut]
    note: str | None


class ProductSupportOut(BaseModel):
    adapter_key: str
    name: str
    url: str | None
    id: uuid.UUID
    section: str
    device_raw: str
    device_names: list[str]
    manufacturer_raw: str | None
    si_codes: list[str]
    is_generic: bool
    details: dict
    manufacturer_matched_by: str | None
    products: list[str]


class TenderIntegrationRequirementOut(BaseModel):
    id: uuid.UUID
    text: str
    criticality: str | None
    # Ключи площадок, названных в требовании; пусто при общем требовании об интеграции.
    platforms: list[str]
    generic: bool


class TenderPlatformOut(UpperSoftwarePlatformOut):
    mentioned: bool


class ManufacturerPlatformCellOut(BaseModel):
    status: str
    devices: list[dict]
    devices_total: int
    note: str | None


class TenderManufacturerSupportOut(BaseModel):
    manufacturer_id: uuid.UUID
    name: str
    is_mirtek: bool
    support: dict[str, ManufacturerPlatformCellOut]


class TenderUpperSoftwareOut(BaseModel):
    """Сводка для карточки закупки: требования об интеграции, названное ПО и матрица
    «производитель × площадка»."""

    requirements: list[TenderIntegrationRequirementOut]
    named_platforms: list[str]
    generic_requirement: bool
    platforms: list[TenderPlatformOut]
    manufacturers: list[TenderManufacturerSupportOut]
