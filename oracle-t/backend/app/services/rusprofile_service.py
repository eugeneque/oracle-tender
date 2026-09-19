"""Синхронизация раздела «Моя компания» с rusprofile.ru (просьба заказчика 18.09.2026).

До этого допуски, реализованные проекты и историю участий вводили руками, а автопоиск давал
только реквизиты. Под учётной записью заказчика сайт отдаёт всё это готовым: лицензии,
список госзакупок с исходом каждой — включая проигрыши, которых нет в реестре контрактов
ЕИС, — численность, финансы, учредителей. Одна кнопка «Обновить из rusprofile» заполняет
профиль, допуски, проекты и историю участий; человеку остаётся проверить.

Правила, которые здесь важны:

1. **Синхронизация не затирает человека.** Поле профиля, которое человек ввёл или подтвердил
   (`field_sources[...] .verified_by_user`), не перезаписывается — сайт заполняет только
   пустое и своё. Ручные лицензии и проекты (без `source: rusprofile`) остаются как есть,
   обновляются только записи, которые синхронизация сама и завела. Заметки к участиям
   (`lessons_learned_md`) не трогаются — это правило общее со выгрузкой из ЕИС.
2. **Досье хранится целиком** (`company_profile.rusprofile_data`) — интерфейс показывает его
   в карточке компании, промпт AI-оценки читает его же (`company_profile_service`), и никто
   не ходит на сайт повторно ради одной цифры.
3. **Скрытые значения — не нули.** Без действующей подписки сайт маскирует данные символом
   «░»; такие значения не сохраняются вовсе, а итог синхронизации предупреждает
   `data_hidden`, чтобы «0 проигрышей» не приняли за факт.
4. История участий синхронизируется только у основной компании: таблица
   `company_participations` привязана к «нашему» производителю, и История считается по ней.
   Для дополнительных компаний обновляются профиль, допуски и проекты.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import rusprofile
from app.adapters.rusprofile import (
    LicenseRecord,
    PurchaseRecord,
    RusprofileAuthError,
    RusprofileDossier,
    RusprofileError,
    RusprofileSession,
)
from app.models.company_participation import (
    CompanyParticipation,
    ParticipationOutcome,
    ParticipationSource,
)
from app.models.company_profile import CompanyProfile
from app.models.integration_setting import RusprofileSettings
from app.models.log import LogLevel
from app.models.user import User
from app.schemas.integration_setting import (
    RusprofileSettingsOut,
    RusprofileSettingsUpdate,
    RusprofileSyncResult,
    YandexConnectionTestResult,
)
from app.services import company_participation_service, company_profile_service
from app.services.audit import log_action

_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Метка источника в `field_sources`, `licenses[].source`, `past_projects[].source`.
SOURCE_RUSPROFILE = "rusprofile"

# Поля профиля, которые синхронизация заполняет из досье: имя поля профиля → имя в досье.
_PROFILE_FIELDS = {
    "legal_name": "legal_name",
    "inn": "inn",
    "kpp": "kpp",
    "ogrn": "ogrn",
    "registration_date": "registration_date",
    "legal_address": "legal_address",
}


class RusprofileNotConfiguredError(RusprofileError):
    """Логин/пароль не заданы — эндпоинт отвечает 400 с подсказкой, где заполнить."""


# --- настройки ------------------------------------------------------------------------------


def _get_or_create(db: Session) -> RusprofileSettings:
    settings = db.get(RusprofileSettings, _SINGLETON_ID)
    if settings is None:
        settings = RusprofileSettings(id=_SINGLETON_ID)
        db.add(settings)
        db.flush()
    return settings


def to_out(db: Session, settings: RusprofileSettings) -> RusprofileSettingsOut:
    updated_by = db.get(User, settings.updated_by_id) if settings.updated_by_id else None
    configured = bool(settings.login and settings.password)
    return RusprofileSettingsOut(
        is_configured=configured,
        login=settings.login,
        has_password=bool(settings.password),
        updated_at=settings.updated_at if (settings.login or settings.password) else None,
        updated_by=updated_by.full_name if updated_by else None,
        last_sync_at=settings.last_sync_at,
        last_sync_status=settings.last_sync_status,
        last_sync_message=settings.last_sync_message,
    )


def get_settings_out(db: Session) -> RusprofileSettingsOut:
    return to_out(db, _get_or_create(db))


def update_settings(
    db: Session, payload: RusprofileSettingsUpdate, *, actor: User
) -> RusprofileSettingsOut:
    """PATCH-семантика: не присланное поле не трогаем, пустая строка — очистка."""

    settings = _get_or_create(db)
    fields_set = payload.model_fields_set
    if "login" in fields_set:
        settings.login = (payload.login or "").strip() or None
    if "password" in fields_set:
        settings.password = payload.password or None
    settings.updated_by_id = actor.id
    log_action(
        db,
        component="integrations",
        action="update_rusprofile_settings",
        result="success",
        details=f"login={'задан' if settings.login else 'пусто'}, password={'задан' if settings.password else 'пусто'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(settings)
    return to_out(db, settings)


def get_credentials(db: Session) -> tuple[str, str]:
    settings = db.get(RusprofileSettings, _SINGLETON_ID)
    if settings is None or not settings.login or not settings.password:
        raise RusprofileNotConfiguredError(
            "Учётная запись rusprofile.ru не настроена: заполните логин и пароль в разделе "
            "«Интеграции → Rusprofile»."
        )
    return settings.login, settings.password


def is_configured(db: Session) -> bool:
    settings = db.get(RusprofileSettings, _SINGLETON_ID)
    return bool(settings and settings.login and settings.password)


def test_connection(db: Session, *, actor: User) -> YandexConnectionTestResult:
    """Вход под сохранёнными учётными данными и проверка, что подписка действует."""

    try:
        login, password = get_credentials(db)
        with RusprofileSession(login, password) as session:
            info = session.check_access()
    except RusprofileError as exc:
        log_action(
            db,
            component="integrations",
            action="test_rusprofile_connection",
            result="error",
            level=LogLevel.WARNING,
            details=str(exc),
            user_id=actor.id,
        )
        db.commit()
        return YandexConnectionTestResult(success=False, message=str(exc))

    if not info.get("logged_in"):
        message = (
            "Вход выполнен, но сайт не показал личный кабинет — возможно, учётную запись "
            "просят подтвердить в браузере."
        )
        success = False
    elif info.get("has_pro") is False:
        message = (
            f"Вход выполнен (пользователь #{info.get('user_id')}), но у учётной записи нет "
            "действующей подписки: закупки и лицензии будут скрыты."
        )
        success = True
    else:
        message = f"Вход выполнен, подписка действует (пользователь #{info.get('user_id')})."
        success = True
    log_action(
        db,
        component="integrations",
        action="test_rusprofile_connection",
        result="success" if success else "error",
        details=message,
        user_id=actor.id,
    )
    db.commit()
    return YandexConnectionTestResult(success=success, message=message)


# --- синхронизация ------------------------------------------------------------------------------


@dataclass
class _Fetched:
    card_id: str
    dossier: RusprofileDossier
    purchases: list[PurchaseRecord]
    purchases_total: int | None
    licenses: list[LicenseRecord]


def _fetch(db: Session, profile: CompanyProfile) -> _Fetched:
    login, password = get_credentials(db)
    with RusprofileSession(login, password) as session:
        card_id = session.resolve_card_id(
            inn=profile.inn, card_hint=profile.rusprofile_card_id
        )
        dossier = session.fetch_dossier(card_id)
        purchases, total = session.fetch_purchases(card_id)
        licenses = session.fetch_licenses(card_id)
    return _Fetched(
        card_id=card_id,
        dossier=dossier,
        purchases=purchases,
        purchases_total=total,
        licenses=licenses,
    )


def _format_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    whole = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{whole} руб."


def _license_item(record: LicenseRecord) -> dict[str, Any]:
    return {
        "name": record.activity or "Лицензия",
        "number": record.number,
        "issued_at": record.issued_at.isoformat() if record.issued_at else None,
        "valid_until": record.valid_until.isoformat() if record.valid_until else None,
        "issuer": record.issuer,
        "source": SOURCE_RUSPROFILE,
    }


def _project_item(record: PurchaseRecord) -> dict[str, Any]:
    parts = [f"Закупка № {record.number}"]
    if record.date:
        parts.append(f"от {record.date.strftime('%d.%m.%Y')}")
    if record.law:
        parts.append(f"({record.law})")
    description = " ".join(parts)
    if record.contract_number:
        description += f"; контракт № {record.contract_number}"
        if record.contract_status:
            description += f", {record.contract_status.lower()}"
    if record.method:
        description += f". {record.method}"
    when = record.contract_date or record.date
    return {
        "work_type": record.subject or f"Закупка № {record.number}",
        "customer": record.customer,
        "volume": _format_money(record.contract_price or record.winner_price),
        "year": when.year if when else None,
        "description": description,
        "source": SOURCE_RUSPROFILE,
    }


def _apply_profile_fields(profile: CompanyProfile, dossier: RusprofileDossier) -> list[str]:
    """Реквизиты из досье — только в пустые поля и в те, что уже были от rusprofile.

    Подтверждённое человеком значение важнее сайта: оно подаётся в evidence оценки как факт,
    и молча заменить его значит подменить факт непроверенным.
    """

    sources: dict[str, Any] = dict(profile.field_sources or {})
    updated: list[str] = []
    for field_name, dossier_key in _PROFILE_FIELDS.items():
        raw = getattr(dossier, dossier_key)
        if not raw:
            continue
        value: Any = raw
        if field_name == "registration_date":
            value = date.fromisoformat(raw)
        current = getattr(profile, field_name)
        source = sources.get(field_name) or {}
        human_owned = bool(current) and (
            source.get("verified_by_user") or source.get("source") == "manual"
        )
        if human_owned and current != value:
            continue
        if current != value:
            setattr(profile, field_name, value)
            updated.append(field_name)
        sources[field_name] = {"source": SOURCE_RUSPROFILE, "verified_by_user": False}

    if not profile.years_of_experience and profile.registration_date:
        years = (date.today() - profile.registration_date).days // 365
        if years > 0:
            profile.years_of_experience = years
            sources["years_of_experience"] = {
                "source": SOURCE_RUSPROFILE,
                "verified_by_user": False,
            }
            updated.append("years_of_experience")
    profile.field_sources = sources
    return updated


def _merge_by_source(existing: list | None, fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ручные записи (без `source: rusprofile`) остаются, записи сайта заменяются целиком."""

    manual = [item for item in (existing or []) if (item or {}).get("source") != SOURCE_RUSPROFILE]
    return manual + fresh


def _sync_participations(
    db: Session, profile: CompanyProfile, fetched: _Fetched, *, now: datetime
) -> tuple[int, int]:
    """Закупки с сайта → `company_participations`. Ключ дедупликации — номер закупки, общий с
    выгрузкой из ЕИС: одна и та же закупка, пришедшая из обоих источников, остаётся одной
    записью, а исход у неё берётся с сайта, потому что ЕИС исходов не знает."""

    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        return 0, 0
    existing = {
        row.external_tender_id: row
        for row in db.scalars(
            select(CompanyParticipation).where(
                CompanyParticipation.manufacturer_id == mirtek.id,
                CompanyParticipation.external_tender_id.is_not(None),
            )
        )
        if row.external_tender_id
    }
    created = updated = 0
    for record in fetched.purchases:
        if not record.number:
            continue
        target = existing.get(record.number)
        if target is None:
            target = CompanyParticipation(
                manufacturer_id=mirtek.id,
                external_tender_id=record.number,
                source=ParticipationSource.RUSPROFILE.value,
            )
            db.add(target)
            existing[record.number] = target
            created += 1
        else:
            updated += 1

        target.tender_title = record.subject or target.tender_title
        target.customer_name = record.customer or target.customer_name
        target.our_inn = profile.inn or target.our_inn
        if record.won is True:
            target.outcome = ParticipationOutcome.WON.value
        elif record.won is False:
            target.outcome = ParticipationOutcome.LOST.value
        elif not target.outcome:
            target.outcome = ParticipationOutcome.UNKNOWN.value
        if record.won and record.winner_price is not None:
            target.our_bid = record.winner_price
        if record.initial_price and record.winner_price and record.won:
            drop = (record.initial_price - record.winner_price) / record.initial_price * 100
            target.price_drop_pct = drop.quantize(Decimal("0.01"))
        if record.participants:
            target.competitors_count = max(len(record.participants) - 1, 0)
        if record.contract_price is not None:
            target.final_contract_value = record.contract_price
        target.executed_at = record.contract_date or record.date or target.executed_at
        target.last_synced_at = now
        # `lessons_learned_md` не трогаем сознательно — это заметка человека.
        if target.tender_id is None:
            matched = company_participation_service.match_tender(db, record.number)
            if matched is not None:
                target.tender_id = matched
    return created, updated


def sync_profile(db: Session, profile: CompanyProfile, *, actor: User) -> RusprofileSyncResult:
    """Кнопка «Обновить из rusprofile»: одна авторизация, все разделы, одна транзакция."""

    settings = _get_or_create(db)
    now = datetime.now(timezone.utc)
    try:
        fetched = _fetch(db, profile)
    except RusprofileError as exc:
        settings.last_sync_at = now
        settings.last_sync_status = "error"
        settings.last_sync_message = str(exc)
        log_action(
            db,
            component="rusprofile",
            action="sync_company",
            result="error",
            level=LogLevel.ERROR,
            details=str(exc),
            user_id=actor.id,
        )
        db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - сбой внешнего сайта не должен ронять запрос
        logger.exception("Синхронизация с rusprofile сорвалась")
        settings.last_sync_at = now
        settings.last_sync_status = "error"
        settings.last_sync_message = str(exc)
        log_action(
            db,
            component="rusprofile",
            action="sync_company",
            result="error",
            level=LogLevel.ERROR,
            details=str(exc),
            user_id=actor.id,
        )
        db.commit()
        raise RusprofileError(f"Не удалось получить данные с rusprofile.ru: {exc}") from exc

    dossier = fetched.dossier
    updated_fields = _apply_profile_fields(profile, dossier)

    profile.licenses = _merge_by_source(
        profile.licenses, [_license_item(item) for item in fetched.licenses]
    )
    wins = [item for item in fetched.purchases if item.won is True]
    losses = [item for item in fetched.purchases if item.won is False]
    wins.sort(key=lambda item: item.contract_date or item.date or date.min, reverse=True)
    profile.past_projects = _merge_by_source(
        profile.past_projects, [_project_item(item) for item in wins]
    )

    data = dossier.to_dict()
    data["licenses"] = [_license_item(item) for item in fetched.licenses]
    data["purchases"] = {
        "fetched": len(fetched.purchases),
        "total_on_site": fetched.purchases_total,
        "wins": len(wins),
        "losses": len(losses),
        "undecided": len(fetched.purchases) - len(wins) - len(losses),
    }
    profile.rusprofile_card_id = fetched.card_id
    profile.rusprofile_data = data
    profile.rusprofile_synced_at = now

    created = updated = 0
    if profile.is_primary:
        created, updated = _sync_participations(db, profile, fetched, now=now)

    # Число в подписи списка на сайте («1–20 из 22») считает и закупки, и их контракты,
    # поэтому сравнивать его с числом закупок нельзя — признак скрытых данных берётся только
    # из масок на карточке.
    hidden = dossier.data_hidden
    summary = (
        f"Карточка {fetched.card_id}: закупок получено {len(fetched.purchases)}"
        f" (побед {len(wins)}, проигрышей {len(losses)}), лицензий {len(fetched.licenses)}, "
        f"реквизитов обновлено {len(updated_fields)}, история участий: создано {created}, "
        f"обновлено {updated}."
    )
    if hidden:
        summary += (
            " Часть данных на сайте скрыта — проверьте, действует ли подписка учётной записи."
        )
    settings.last_sync_at = now
    settings.last_sync_status = "ok"
    settings.last_sync_message = summary
    log_action(
        db,
        component="rusprofile",
        action="sync_company",
        result="ok",
        details=summary,
        user_id=actor.id,
    )
    db.commit()
    db.refresh(profile)
    return RusprofileSyncResult(
        card_id=fetched.card_id,
        source_url=dossier.source_url,
        profile_fields_updated=updated_fields,
        licenses_total=len(profile.licenses or []),
        projects_total=len(profile.past_projects or []),
        purchases_fetched=len(fetched.purchases),
        purchases_total_on_site=fetched.purchases_total,
        participations_created=created,
        participations_updated=updated,
        wins=len(wins),
        losses=len(losses),
        data_hidden=hidden,
        message=summary,
    )


__all__ = [
    "RusprofileAuthError",
    "RusprofileError",
    "RusprofileNotConfiguredError",
    "get_settings_out",
    "is_configured",
    "sync_profile",
    "test_connection",
    "update_settings",
]
