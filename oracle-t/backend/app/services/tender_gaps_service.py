"""Дозаполнение полей, по которым фильтруют список тендеров (замечание тестировщика
16.09.2026: «не работает фильтр по коду ОКПД, регионы и т.д.»).

Фильтры работали — фильтровать было не по чему: ОКПД2 стоял у 13 закупок из 17 560,
регион у 9 %, тип конкурса у 38. Все три поля заполнялись ИИ-анализом документации,
который запускают по единицам закупок, либо карточкой с сайта ЕИС при первом открытии
тендера человеком. Для фильтра это бесполезно: он применяется к списку, а не к открытым
карточкам.

Три источника, ни один не ходит в модель:

1. **Сохранённые карточки** (`backfill_from_stored_cards`) — без сети. Карточек в базе
   полторы тысячи, и у трети из них в таблице лотов стоит «Классификация по ОКПД2», а в
   реквизитах заказчика — адрес и ИНН; дозаполнение полей тендера из карточки появилось
   позже, чем эти карточки были сохранены, и они так и лежали непрочитанными.
2. **Карточки с сайта** (`fetch_missing_cards`) — по сети, для открытых закупок, прошедших
   профиль релевантности, у которых карточки ещё нет. Именно они стоят в списке по
   умолчанию, и именно к ним применяют фильтр. Порциями и с паузой: это чужой сайт.
3. **Тип конкурса по наименованию** (`tender_type_from_title`) — Приложение E ТЗ по
   ключевым словам: «поверка» → поверка, «поставка» + «монтаж/установка» → комплекс,
   «поставка» → поставка, «работы/замена/монтаж» → работы. Это предварительная оценка
   для фильтра; ИИ-анализ, когда его запустят, перезаписывает поле своим вердиктом
   (`tender_analysis`), и это правильно — он читает ТЗ, а не заголовок.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.tender import Tender, TenderStatus, TenderType
from app.models.tender_card import TenderCard
from app.services import tender_card_service
from app.services.audit import log_action

COMPONENT = "tender_gaps"

# Пауза между запросами карточек к сайту ЕИС: тот же ритм, что у CLI `fill-regions`.
FETCH_DELAY_SECONDS = 1.5

_REVERIFICATION_RE = re.compile(r"поверк", re.IGNORECASE)
_SUPPLY_RE = re.compile(r"поставк|приобретен|закупк[аи]\s+(?:счетчик|счётчик|прибор)", re.IGNORECASE)
_WORKS_RE = re.compile(
    r"\bработ|монтаж|установк|замен[аеуы]|демонтаж|строительств|реконструкц|модернизац|"
    r"техническ\w+\s+обслуживан|внедрен",
    re.IGNORECASE,
)


@dataclass
class GapsOutcome:
    tenders_seen: int = 0
    okpd2_filled: int = 0
    region_filled: int = 0
    type_filled: int = 0
    cards_fetched: int = 0
    cards_failed: int = 0
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"просмотрено {self.tenders_seen}, ОКПД2 заполнен у {self.okpd2_filled}, регион у "
            f"{self.region_filled}, тип конкурса у {self.type_filled}, карточек получено "
            f"{self.cards_fetched}, не получено {self.cards_failed}"
        )


def tender_type_from_title(title: str | None) -> str | None:
    """Тип конкурса по наименованию закупки (Приложение E ТЗ) — грубо, но для фильтра
    достаточно; пустое наименование или отсутствие ключевых слов — `None`, а не «прочее»:
    «прочее» — тоже вердикт, и ставить его по незнанию нельзя."""

    if not title:
        return None
    if _REVERIFICATION_RE.search(title):
        return TenderType.REVERIFICATION.value
    supply = _SUPPLY_RE.search(title) is not None
    works = _WORKS_RE.search(title) is not None
    if supply and works:
        return TenderType.COMPLEX.value
    if supply:
        return TenderType.SUPPLY_ONLY.value
    if works:
        return TenderType.WORKS_ONLY.value
    return None


def fill_type_from_title(tender: Tender) -> bool:
    if tender.tender_type:
        return False
    guess = tender_type_from_title(tender.title)
    if guess is None:
        return False
    tender.tender_type = guess
    return True


def _apply_card(db: Session, tender: Tender, payload: dict, outcome: GapsOutcome) -> None:
    had_okpd2, had_region = tender.okpd2_code, tender.region_organizer_code
    tender_card_service.fill_gaps_from_card(db, tender, payload)
    if tender.okpd2_code and tender.okpd2_code != had_okpd2:
        outcome.okpd2_filled += 1
    if tender.region_organizer_code and not had_region:
        outcome.region_filled += 1


def backfill_from_stored_cards(db: Session, *, limit: int | None = None) -> GapsOutcome:
    """Проходит по тендерам с сохранённой карточкой и пустыми полями — без обращений к
    сайту. Заодно ставит тип конкурса по наименованию всем, у кого его нет."""

    outcome = GapsOutcome()
    query = (
        select(Tender, TenderCard)
        .join(TenderCard, TenderCard.tender_id == Tender.id)
        .where(or_(Tender.okpd2_code.is_(None), Tender.region_organizer_code.is_(None)))
        .order_by(Tender.created_at.desc())
    )
    if limit:
        query = query.limit(limit)
    for tender, card in db.execute(query).all():
        outcome.tenders_seen += 1
        _apply_card(db, tender, card.payload or {}, outcome)
    db.flush()

    typed = 0
    for tender in db.scalars(select(Tender).where(Tender.tender_type.is_(None))):
        if fill_type_from_title(tender):
            typed += 1
    outcome.type_filled = typed
    db.commit()
    return outcome


def _open_relevant_without_card(db: Session, limit: int) -> list[Tender]:
    """Открытые закупки, прошедшие профиль, у которых карточки ещё нет, — свежие первыми."""

    return list(
        db.scalars(
            select(Tender)
            .outerjoin(TenderCard, TenderCard.tender_id == Tender.id)
            .where(
                TenderCard.tender_id.is_(None),
                or_(Tender.passed_relevance_filter.is_(None), Tender.passed_relevance_filter.is_(True)),
                or_(Tender.application_end.is_(None), Tender.application_end >= func.now()),
                or_(
                    Tender.status.is_(None),
                    Tender.status.notin_([TenderStatus.COMPLETED.value, TenderStatus.CANCELLED.value]),
                ),
                or_(Tender.okpd2_code.is_(None), Tender.region_organizer_code.is_(None)),
            )
            .order_by(Tender.created_at.desc())
            .limit(limit)
        )
    )


def fetch_missing_cards(
    db: Session, *, limit: int = 100, delay: float = FETCH_DELAY_SECONDS
) -> GapsOutcome:
    """Забирает карточки с сайта для открытых релевантных закупок без карточки и заполняет
    из них пропуски. Недоступность одной карточки не останавливает остальные."""

    outcome = GapsOutcome()
    for tender in _open_relevant_without_card(db, limit):
        outcome.tenders_seen += 1
        had_okpd2, had_region = tender.okpd2_code, tender.region_organizer_code
        try:
            card = tender_card_service.sync_card(db, tender)
        except Exception as exc:  # noqa: BLE001 - одна карточка не должна ронять проход
            db.rollback()
            outcome.cards_failed += 1
            logger.warning(f"Карточка {tender.external_id} для дозаполнения не получена: {exc}")
            continue
        if card is None:
            outcome.cards_failed += 1
        else:
            outcome.cards_fetched += 1
            db.refresh(tender)
            if tender.okpd2_code and tender.okpd2_code != had_okpd2:
                outcome.okpd2_filled += 1
            if tender.region_organizer_code and not had_region:
                outcome.region_filled += 1
        if delay:
            time.sleep(delay)
    return outcome


def run(db: Session, *, fetch_limit: int = 100, delay: float = FETCH_DELAY_SECONDS) -> GapsOutcome:
    """Полный проход: сначала то, что уже лежит в базе, потом сеть."""

    stored = backfill_from_stored_cards(db)
    fetched = fetch_missing_cards(db, limit=fetch_limit, delay=delay)
    total = GapsOutcome(
        tenders_seen=stored.tenders_seen + fetched.tenders_seen,
        okpd2_filled=stored.okpd2_filled + fetched.okpd2_filled,
        region_filled=stored.region_filled + fetched.region_filled,
        type_filled=stored.type_filled,
        cards_fetched=fetched.cards_fetched,
        cards_failed=fetched.cards_failed,
    )
    log_action(
        db,
        component=COMPONENT,
        action="fill_gaps",
        result="success",
        level=LogLevel.INFO,
        details=total.summary(),
    )
    db.commit()
    return total
