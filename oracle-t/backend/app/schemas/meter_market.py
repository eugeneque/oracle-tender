"""Тела запросов справочника рынка ПУ (раздел 79 ARCHITECTURE.md). Ответы отчёта — словари
сервиса `meter_market_report`: у экранов разная форма, и дублировать её схемами ради одной
страницы смысла нет."""

from datetime import date

from pydantic import BaseModel, Field


class VariantMapIn(BaseModel):
    modification_id: int | None = None
    # Свести туда же неразмеченные написания того же типа с тем же ключом нормализации.
    apply_same_key: bool = True


class ModificationIn(BaseModel):
    name: str | None = None
    phase: str | None = None
    design: str | None = None
    interface: str | None = None
    mpi_years: int | None = None
    comment: str | None = None
    manufacturer_override_id: int | None = None
    in_rpp: bool | None = None
    score: float | None = None
    rpp_included_at: date | None = None
    rpp_record_number: str | None = None


class ModificationFromVariantIn(ModificationIn):
    variant_id: int


class TypeIn(BaseModel):
    review_status: str | None = None
    manufacturer_id: int | None = None
    cert_valid_to: date | None = None
    mpi_years: int | None = None
    notation: str | None = None


class ManufacturerIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class GroupMemberIn(BaseModel):
    manufacturer_id: int
    year_from: int | None = None
    year_to: int | None = None


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    members: list[GroupMemberIn] = []


class ThresholdIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    min_score: float = Field(ge=0)


class ThresholdsIn(BaseModel):
    thresholds: list[ThresholdIn]
