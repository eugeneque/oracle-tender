"""Схемы «Интеграции МИРТЕК в ПО верхнего уровня» (данные специалиста, 05.11.2026)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FieldIn(BaseModel):
    value: str = ""
    verdict: str | None = None
    # Подтверждение без правки значения: сервер переводит поле в `confirmed`.
    status: str | None = None


class FunctionIn(BaseModel):
    id: str | None = None
    model: str = ""
    protocol: str = ""
    name: str
    status: str = "yes"
    comment: str = ""
    verified: bool = False
    source: str | None = None
    document_id: str | None = None


class DraftIn(BaseModel):
    fields: dict[str, FieldIn] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    functions: list[FunctionIn] = Field(default_factory=list)


class IntegrationUpdateIn(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    platform_key: str | None = None
    draft: DraftIn | None = None


class IntegrationCreateIn(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    platform_key: str | None = None


class PublishIn(BaseModel):
    comment: str | None = None


class IntegrationOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    platform_key: str | None
    platform_name: str | None
    aliases: list[str]
    sort_order: int
    draft: dict
    published_at: datetime | None
    draft_updated_at: datetime | None
    dirty: bool
    pending_review: int
    has_published: bool


class FieldMetaOut(BaseModel):
    key: str
    label: str
    with_verdict: bool


class PublicationOut(BaseModel):
    id: uuid.UUID
    published_at: datetime
    published_by: str | None
    comment: str | None
    changes: list[dict]


class IntegrationsOverviewOut(BaseModel):
    items: list[IntegrationOut]
    fields: list[FieldMetaOut]
    publications: list[PublicationOut]
    # Публичные списки, к которым можно привязать ПО (автозаполнение).
    platforms: list[dict]


class PublishOutcomeOut(BaseModel):
    published: bool
    changed: int
    message: str


class DocumentOut(BaseModel):
    id: uuid.UUID
    integration_id: uuid.UUID
    file_name: str
    content_type: str | None
    size: int
    analysis_status: str
    analysis_result: dict | None
    analysis_error: str | None
    analyzed_at: datetime | None
    created_at: datetime


class ProductIntegrationOut(BaseModel):
    integration_id: uuid.UUID
    key: str
    name: str
    matched_models: list[str]
    has_mirtek: str
    has_mirtek_verdict: str | None
    protocol_mirtek: str
    protocol_spodes: str
    software_version: str
    functions_total: int
    functions_supported: int
    published_at: datetime | None


class TenderIntegrationOut(BaseModel):
    integration_id: uuid.UUID
    name: str
    has_mirtek: str
    has_mirtek_verdict: str | None
    protocol_spodes: str
    software_version: str
    published_at: datetime | None
