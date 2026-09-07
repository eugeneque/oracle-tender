"""История участий МИРТЕК: хранение, сводка и выгрузка из реестра контрактов ЕИС
(разделы 5.5.1, 5.6, 7 ТЗ; источник заменён 04.09.2026).

Основной источник измерения History. Три вещи, которые здесь важны:

1. **Выгрузка не затирает человека.** Повторный импорт обновляет данные закупки, но не
   трогает `lessons_learned_md` и не отменяет ручную привязку к тендеру: заметку писал
   человек, а внешний источник её не знает.
2. **Сводка считает `lost` и `disqualified` раздельно** (раздел 7 ТЗ). Win-rate берётся от
   числа участий с известным исходом — иначе неразобранные записи занижали бы его молча.
3. **Источник знает только о победах.** В реестре контрактов ЕИС лежат заключённые
   контракты; проигрышей там нет по устройству. Сводка честно показывает, что доля побед
   посчитана по выборке без поражений, а History по такой выборке долю побед вовсе не
   считает (`ai_profile_service`).

Прежний вариант — синхронизация с OPTI — снят 04.09.2026: у сервиса нет ни публичного API,
ни выдаваемых нам токенов, а строить рабочую функцию на чужой браузерной сессии нельзя.
Реестр контрактов ЕИС открыт и не требует ни учётной записи, ни ключа.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.adapters.eis_contracts import (
    EisContractsAdapter,
    EisContractsError,
    ParticipationRecord,
)
from app.adapters.eis_results import EisResultsError, fetch_outcome
from app.models.company_participation import (
    WINS_ONLY_SOURCES,
    CompanyParticipation,
    ParticipationOutcome,
    ParticipationSource,
)
from app.models.log import LogLevel
from app.models.tender import Tender, TenderStage
from app.models.user import User
from app.services import company_profile_service
from app.services.audit import log_action


class ParticipationError(RuntimeError):
    """Ошибка, которую эндпоинт превращает в 4xx с текстом для администратора."""


# --- чтение и сводка -----------------------------------------------------------------------


def list_participations(db: Session, *, limit: int = 200, offset: int = 0) -> list[CompanyParticipation]:
    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        return []
    return list(
        db.scalars(
            select(CompanyParticipation)
            .where(CompanyParticipation.manufacturer_id == mirtek.id)
            .order_by(
                CompanyParticipation.executed_at.desc().nullslast(),
                CompanyParticipation.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )


@dataclass
class ParticipationSummary:
    """Сводка вкладки «История участий» (раздел 5.6 ТЗ)."""

    total: int = 0
    won: int = 0
    lost: int = 0
    disqualified: int = 0
    unknown: int = 0
    win_rate: Decimal | None = None
    last_synced_at: datetime | None = None


def summary(db: Session) -> ParticipationSummary:
    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        return ParticipationSummary()

    rows = db.execute(
        select(CompanyParticipation.outcome, func.count())
        .where(CompanyParticipation.manufacturer_id == mirtek.id)
        .group_by(CompanyParticipation.outcome)
    ).all()
    counts = {outcome: count for outcome, count in rows}

    result = ParticipationSummary(
        total=sum(counts.values()),
        won=counts.get(ParticipationOutcome.WON.value, 0),
        lost=counts.get(ParticipationOutcome.LOST.value, 0),
        disqualified=counts.get(ParticipationOutcome.DISQUALIFIED.value, 0),
        unknown=counts.get(ParticipationOutcome.UNKNOWN.value, 0),
    )
    # Знаменатель — участия с известным исходом. Записи «исход неизвестен» ни победа, ни
    # поражение: включить их в знаменатель значит занизить win-rate за счёт неполноты данных.
    decided = result.won + result.lost + result.disqualified
    if decided:
        result.win_rate = Decimal(f"{result.won / decided * 100:.2f}")
    result.last_synced_at = db.scalar(
        select(func.max(CompanyParticipation.last_synced_at)).where(
            CompanyParticipation.manufacturer_id == mirtek.id
        )
    )
    return result


# --- ручное ведение записей -----------------------------------------------------------------


_EDITABLE_FIELDS = (
    "tender_id",
    "external_tender_id",
    "tender_title",
    "customer_name",
    "customer_org_id",
    "our_inn",
    "our_bid",
    "price_drop_pct",
    "competitors_count",
    "outcome",
    "final_contract_value",
    "executed_at",
    "lessons_learned_md",
)


def create_participation(
    db: Session, payload: dict[str, Any], *, actor: User
) -> CompanyParticipation:
    """Ручное добавление записи, не покрытой синхронизацией (раздел 5.6 ТЗ)."""

    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        raise ParticipationError(
            "В справочнике производителей нет записи МИРТЕК — историю участий привязывать не "
            "к чему."
        )
    profile = company_profile_service.get_profile(db)
    record = CompanyParticipation(
        manufacturer_id=mirtek.id,
        source=ParticipationSource.MANUAL.value,
        our_inn=payload.get("our_inn") or (profile.inn if profile else None),
    )
    for field_name in _EDITABLE_FIELDS:
        if field_name in payload:
            setattr(record, field_name, payload[field_name])
    if not record.outcome:
        record.outcome = ParticipationOutcome.UNKNOWN.value
    db.add(record)
    log_action(
        db,
        component="company_participations",
        action="create",
        result="ok",
        details=f"Ручная запись: {record.tender_title or record.external_tender_id or '—'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(record)
    return record


def update_participation(
    db: Session, participation_id: uuid.UUID, payload: dict[str, Any], *, actor: User
) -> CompanyParticipation:
    record = db.get(CompanyParticipation, participation_id)
    if record is None:
        raise ParticipationError("Запись истории участий не найдена")
    for field_name in _EDITABLE_FIELDS:
        if field_name in payload:
            setattr(record, field_name, payload[field_name])
    log_action(
        db,
        component="company_participations",
        action="update",
        result="ok",
        details=str(participation_id),
        user_id=actor.id,
    )
    db.commit()
    db.refresh(record)
    return record


def delete_participation(db: Session, participation_id: uuid.UUID, *, actor: User) -> None:
    record = db.get(CompanyParticipation, participation_id)
    if record is None:
        raise ParticipationError("Запись истории участий не найдена")
    db.delete(record)
    log_action(
        db,
        component="company_participations",
        action="delete",
        result="ok",
        details=str(participation_id),
        user_id=actor.id,
    )
    db.commit()


# --- синхронизация -------------------------------------------------------------------------


@dataclass
class SyncResult:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    matched_tenders: int = 0
    # Проигрыши, выведенные из пайплайна: их нет ни в одном открытом реестре, см.
    # `derive_outcomes_from_pipeline`.
    losses_found: int = 0
    checked_submitted: int = 0


def _match_tender(db: Session, record: ParticipationRecord) -> uuid.UUID | None:
    """Пытается связать запись с тендером нашей базы по номеру закупки.

    Совпадения может не быть, и это норма: история покрывает годы, когда система ещё не
    работала. Связь нужна лишь для того, чтобы из карточки тендера был виден наш прошлый
    заход на ту же закупку.
    """

    if not record.external_tender_id:
        return None
    return db.scalar(
        select(Tender.id).where(
            or_(
                Tender.external_id == record.external_tender_id,
                Tender.registry_number == record.external_tender_id,
            )
        )
    )


def sync_from_eis(db: Session, *, actor: User) -> SyncResult:
    """Выгружает контракты компании из реестра ЕИС по ИНН из профиля и обновляет таблицу.

    Ключ дедупликации — `external_tender_id`: повторный запуск не плодит дубли, а обновляет
    уже сохранённые записи. Поля, которые ведёт человек, при этом не перезаписываются.

    Источник даёт только победы (см. докстринг модуля), поэтому исход у всех новых записей —
    `won`. Ручные записи о проигрышах он не трогает: их идентификаторы в выдаче не
    встречаются, а если и встретятся, `unknown` из источника исход не затирает.
    """

    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        raise ParticipationError(
            "В справочнике производителей нет записи МИРТЕК — выгружать историю не для кого."
        )
    profile = company_profile_service.get_profile(db)
    if profile is None or not profile.has_inn():
        raise ParticipationError(
            "В профиле компании не указан ИНН, а реестр контрактов ищется именно по нему. "
            "Заполните ИНН в разделе «Настройки → Моя компания → Профиль компании»."
        )

    inn = profile.inn.strip()
    try:
        records = EisContractsAdapter().fetch_participations(inn)
    except EisContractsError as exc:
        log_action(
            db,
            component="eis_contracts",
            action="sync_participations",
            result="error",
            level=LogLevel.ERROR,
            details=str(exc),
            user_id=actor.id if actor else None,
        )
        db.commit()
        raise ParticipationError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сбой внешнего сервиса не должен ронять запрос
        logger.exception("Выгрузка истории участий из реестра контрактов ЕИС сорвалась")
        log_action(
            db,
            component="eis_contracts",
            action="sync_participations",
            result="error",
            level=LogLevel.ERROR,
            details=str(exc),
            user_id=actor.id if actor else None,
        )
        db.commit()
        raise ParticipationError(
            f"Не удалось получить историю участий из реестра контрактов ЕИС: {exc}"
        ) from exc

    now = datetime.now(timezone.utc)
    result = SyncResult(fetched=len(records))
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

    for record in records:
        target = existing.get(record.external_tender_id) if record.external_tender_id else None
        if target is None:
            target = CompanyParticipation(
                manufacturer_id=mirtek.id,
                external_tender_id=record.external_tender_id,
                source=ParticipationSource.EIS_CONTRACTS.value,
            )
            db.add(target)
            if record.external_tender_id:
                existing[record.external_tender_id] = target
            result.created += 1
        else:
            result.updated += 1

        target.tender_title = record.tender_title or target.tender_title
        target.customer_name = record.customer_name or target.customer_name
        target.our_inn = inn
        # `unknown` из источника не затирает исход, который человек проставил руками.
        if record.outcome != ParticipationOutcome.UNKNOWN.value:
            target.outcome = record.outcome
        elif not target.outcome:
            target.outcome = ParticipationOutcome.UNKNOWN.value
        target.final_contract_value = (
            record.final_contract_value
            if record.final_contract_value is not None
            else target.final_contract_value
        )
        target.executed_at = record.executed_at or target.executed_at
        target.last_synced_at = now
        # `lessons_learned_md` не трогаем сознательно — это заметка человека.

        if target.tender_id is None:
            matched = _match_tender(db, record)
            if matched is not None:
                target.tender_id = matched
                result.matched_tenders += 1

    # Второй шаг той же кнопки: победы пришли из реестра контрактов, проигрыши там
    # отсутствуют по устройству — их выводим из собственного пайплайна.
    result.checked_submitted, result.losses_found = derive_outcomes_from_pipeline(
        db, actor=actor
    )

    log_action(
        db,
        component="eis_contracts",
        action="sync_participations",
        result="ok",
        details=(
            f"Получено {result.fetched}, создано {result.created}, обновлено {result.updated}, "
            f"сопоставлено с тендерами {result.matched_tenders}, "
            f"проигрышей из пайплайна {result.losses_found}"
        ),
        user_id=actor.id if actor else None,
    )
    db.commit()
    return result


def has_only_wins_source(db: Session) -> bool:
    """Состоит ли история целиком из источников, которые не знают о поражениях.

    Нужно интерфейсу: показать рядом со сводкой предупреждение, что win-rate посчитан по
    выборке, куда проигрыши физически не попадали. Молчаливые 100% в такой ситуации —
    самая дорогая из возможных ошибок этого раздела.
    """

    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        return False
    sources = set(
        db.scalars(
            select(CompanyParticipation.source)
            .where(CompanyParticipation.manufacturer_id == mirtek.id)
            .distinct()
        )
    )
    return bool(sources) and sources.issubset(WINS_ONLY_SOURCES)


def _is_us(winner_name: str | None, profile, mirtek) -> bool:
    """Мы ли получили контракт.

    Сравнение по названию, а не по ИНН: в таблице результатов ЕИС ИНН победителя нет, есть
    только наименование. Поэтому сверяем по нескольким написаниям сразу — полному
    юридическому имени из профиля, бренду и короткому названию производителя, — и приводим
    к одному регистру без кавычек, которые площадки ставят вразнобой.
    """

    if not winner_name:
        return False
    normalized = re.sub(r"[«»\"']", "", winner_name).upper()
    candidates = [
        profile.legal_name if profile else None,
        mirtek.legal_name if mirtek else None,
        mirtek.brand_name if mirtek else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        cleaned = re.sub(r"[«»\"']", "", candidate).upper().strip()
        # Короткие бренды («МИРТЕК») ищем как подстроку, полные наименования — тоже: у ЕИС
        # написание с «ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ» впереди.
        if len(cleaned) >= 4 and cleaned in normalized:
            return True
    return False


def derive_outcomes_from_pipeline(db: Session, *, actor: User) -> tuple[int, int]:
    """Определяет исход закупок, где мы подавали заявку, и записывает проигрыши.

    Зачем это нужно отдельным шагом: **проигрыши не публикуются**. Реестра протоколов с
    поиском по участнику в ЕИС нет, а в протоколах электронных процедур участники обезличены
    идентификационными номерами заявок — раскрывается только победитель. Значит, «мы
    проиграли» нельзя выгрузить, но можно вычислить: закупка, которую наш тендерный отдел
    отметил как «заявка подана», завершилась, а контракт достался не нам.

    Оба факта внешние по отношению к оценке результата: победитель берётся из ЕИС, а участие
    — из собственного пайплайна, куда его ставят до того, как исход стал известен.

    Возвращает `(проверено закупок, записано проигрышей)`.
    """

    mirtek = company_profile_service.get_mirtek(db)
    if mirtek is None:
        return 0, 0
    profile = company_profile_service.get_profile(db)

    # Берём только закупки с реестровым номером: без него итог в ЕИС не запросить.
    submitted = db.scalars(
        select(Tender).where(
            Tender.stage == TenderStage.APPLICATION_SUBMITTED.value,
            Tender.registry_number.is_not(None),
        )
    ).all()
    if not submitted:
        return 0, 0

    known_tender_ids = set(
        db.scalars(
            select(CompanyParticipation.tender_id).where(
                CompanyParticipation.manufacturer_id == mirtek.id,
                CompanyParticipation.tender_id.is_not(None),
            )
        )
    )

    now = datetime.now(timezone.utc)
    losses = 0
    checked = 0
    for tender in submitted:
        if tender.id in known_tender_ids:
            continue
        checked += 1
        try:
            outcome = fetch_outcome(tender.registry_number)
        except EisResultsError as exc:
            # Одна недоступная закупка не должна прерывать разбор остальных (раздел 5.9 ТЗ).
            logger.warning(f"Итог закупки {tender.registry_number} не получен: {exc}")
            continue

        if outcome.winner_name is None:
            # Контракт ещё не заключён — исход неизвестен, и записывать нечего.
            continue
        if _is_us(outcome.winner_name, profile, mirtek):
            # Победы приходят из реестра контрактов, здесь их дублировать не нужно.
            continue

        db.add(
            CompanyParticipation(
                manufacturer_id=mirtek.id,
                tender_id=tender.id,
                external_tender_id=tender.registry_number,
                tender_title=tender.title,
                customer_name=tender.customer_name,
                our_inn=profile.inn if profile else None,
                outcome=ParticipationOutcome.LOST.value,
                final_contract_value=outcome.contract_value,
                executed_at=None,
                lessons_learned_md=f"Контракт получил: {outcome.winner_name}",
                source=ParticipationSource.EIS_RESULTS.value,
                last_synced_at=now,
            )
        )
        losses += 1

    if losses:
        log_action(
            db,
            component="eis_results",
            action="derive_losses",
            result="ok",
            details=f"Проверено закупок с поданной заявкой {checked}, записано проигрышей {losses}",
            user_id=actor.id if actor else None,
        )
    return checked, losses
