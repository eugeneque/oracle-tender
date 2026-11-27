"""Пополнение справочника приборов «Рынка ПУ» из реестра утверждённых типов СИ (27.11.2026)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis import FgisAdapter
from app.models.meter_market import (
    MeterMarketManufacturer,
    MeterMarketModification,
    MeterMarketType,
    TypeReviewStatus,
)
from app.services.meter_market_loader import is_ee_title


@dataclass
class SyncStats:
    found: int = 0
    types_created: int = 0
    types_updated: int = 0
    modifications_created: int = 0
    skipped_foreign: int = 0


def sync_manufacturer(
    db: Session,
    manufacturer: MeterMarketManufacturer,
    *,
    adapter: FgisAdapter | None = None,
) -> SyncStats:
    """Догрузить электросчётчики производителя, не включая новые типы в отчёт автоматически."""

    adapter = adapter or FgisAdapter()
    results = adapter.search_by_manufacturer(manufacturer.name, fetch_cards=True)
    stats = SyncStats()
    for result in results:
        if not is_ee_title(result.type_name):
            stats.skipped_foreign += 1
            continue
        stats.found += 1
        meter_type = db.scalar(select(MeterMarketType).where(MeterMarketType.mit_number == result.si_code))
        if meter_type is None:
            meter_type = MeterMarketType(
                mit_number=result.si_code,
                manufacturer_id=manufacturer.id,
                review_status=TypeReviewStatus.NEW.value,
                in_report=False,
            )
            db.add(meter_type)
            stats.types_created += 1
        else:
            stats.types_updated += 1
            if meter_type.manufacturer_id is None:
                meter_type.manufacturer_id = manufacturer.id
        meter_type.title = result.type_name or meter_type.title
        meter_type.notation = result.notation or meter_type.notation
        meter_type.cert_valid_to = result.valid_to or meter_type.cert_valid_to
        if result.mpi_months and meter_type.mpi_years is None:
            meter_type.mpi_years = max(1, round(result.mpi_months / 12))
        if result.description_type_version != meter_type.description_type_version:
            meter_type.description_type_text = None
            meter_type.description_type_fetched_at = None
        meter_type.description_type_version = result.description_type_version or meter_type.description_type_version
        meter_type.description_type_url = result.description_type_url or meter_type.description_type_url
        meter_type.description_type_mirror_url = result.description_type_mirror_url or meter_type.description_type_mirror_url
        db.flush()
        existing = set(
            db.scalars(select(MeterMarketModification.name).where(MeterMarketModification.type_id == meter_type.id)).all()
        )
        for name in result.tested_modifications:
            name = " ".join(name.split()).strip()
            if not name or name in existing:
                continue
            db.add(MeterMarketModification(type_id=meter_type.id, name=name))
            existing.add(name)
            stats.modifications_created += 1
    db.flush()
    return stats
