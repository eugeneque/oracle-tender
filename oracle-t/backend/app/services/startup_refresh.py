"""Актуализация сразу после запуска (просьба заказчика 28.09.2026).

Новая установка приходит со снимком данных (`app/seed/snapshot.py`), но снимок снят в
какой-то день, а каталог производителей и досье «Моей компании» с тех пор могли
измениться. Поэтому, как только backend поднялся, в фоне:

1. **Каталог продукции** — полный опрос всех производителей (`catalog_autofill`: ФГИС →
   сайт → привязка → Аршин, затем характеристики), тем же путём, что и ночной запуск.
2. **«Моя компания»** — синхронизация каждой компании с rusprofile.ru. Если учётная запись
   не заведена, читается открытая часть карточки, и данные только дополняются
   (`rusprofile_service`, правило 5).

Перезапуск — не повод опрашивать всё заново: `--reload` на рабочей машине перезапускает
backend на каждую правку, а обновление с GitHub — раз в сутки. Шаг пропускается, если
такой же проход закончился меньше `STARTUP_REFRESH_MIN_INTERVAL_HOURS` часов назад;
оборванный перезапуском опрос каталога продолжает `catalog_autofill.resume_pending`.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.catalog_queue import CatalogLookupTask
from app.models.company_profile import CompanyProfile


def _recent(moment: datetime | None, hours: int) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - moment < timedelta(hours=hours)


def refresh_catalog(hours: int) -> int | None:
    """Ставит полный опрос каталога. `None` — пропущен: опрос идёт или был недавно."""

    from app.services import catalog_autofill

    db = SessionLocal()
    try:
        if catalog_autofill.current_run_started_at(db) is not None:
            logger.info("Актуализация каталога при старте: опрос уже идёт — продолжится сам")
            return None
        last = db.scalar(
            select(func.max(CatalogLookupTask.finished_at)).where(
                CatalogLookupTask.adapter_key == catalog_autofill.ADAPTER_KEY
            )
        )
    finally:
        db.close()
    if _recent(last, hours):
        logger.info(f"Актуализация каталога при старте пропущена: последний опрос {last:%d.%m %H:%M}")
        return None
    queued = catalog_autofill.run_all_in_background()
    logger.info(f"Актуализация каталога при старте: в очереди производителей {queued}")
    return queued


def refresh_companies(hours: int) -> list[str]:
    """Синхронизирует с rusprofile каждую компанию с ИНН или ссылкой на карточку."""

    from app.services import rusprofile_service

    results: list[str] = []
    db = SessionLocal()
    try:
        profiles = list(
            db.scalars(select(CompanyProfile).order_by(CompanyProfile.is_primary.desc()))
        )
        for profile in profiles:
            name = profile.legal_name or profile.inn or str(profile.id)
            if not (profile.inn or profile.rusprofile_card_id):
                continue
            if _recent(profile.rusprofile_synced_at, hours):
                results.append(f"{name}: пропущена, обновлялась недавно")
                continue
            try:
                result = rusprofile_service.sync_profile(
                    db, profile, actor=None, allow_anonymous=True
                )
                results.append(f"{name}: {result.message}")
            except Exception as exc:  # noqa: BLE001 - сбой одной компании не останавливает остальные
                db.rollback()
                results.append(f"{name}: не удалось — {exc}")
    finally:
        db.close()
    for line in results:
        logger.info(f"Актуализация «Моей компании» при старте: {line}")
    return results


def _run() -> None:
    hours = get_settings().startup_refresh_min_interval_hours
    try:
        refresh_catalog(hours)
    except Exception as exc:  # noqa: BLE001 - фоновый поток, падение не должно пропасть молча
        logger.exception(f"Актуализация каталога при старте не запустилась: {exc}")
    try:
        refresh_companies(hours)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Актуализация «Моей компании» при старте не удалась: {exc}")


def start() -> None:
    settings = get_settings()
    if not (settings.scheduler_enabled and settings.startup_refresh_enabled):
        return
    threading.Thread(target=_run, name="startup-refresh", daemon=True).start()
