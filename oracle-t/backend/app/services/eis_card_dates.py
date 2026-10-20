"""Даты процедуры из карточки закупки ЕИС (`tender_cards.payload`), раздел 68 ARCHITECTURE.md.

Карточка — единственный источник, где срок подачи заявок указан точно: с часами и поясом
заказчика («29.09.2026 09:00 (МСК+4)»). Площадки и каналы дают его хуже: выдача ЕИС — только
дату, Госплан — местное время заказчика без пояса. Отсюда же даты берёт выгрузка сделки в
Bitrix24 (`bitrix_ai_service.build_deal_fields`), поэтому разбор вынесен в общий модуль
20.10.2026.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_card import TenderCard

MSK = ZoneInfo("Europe/Moscow")

# Даты процедуры из карточки ЕИС (первая выгрузка 20.10.2026: в сделке были пусты начало
# подачи и подведение итогов). В модели тендера есть только сроки подачи, и окончание — лишь
# датой из списка выдачи; карточка даёт точные значения с часовым поясом заказчика.
# Ключ — поле пакета, значение — начала подписей в карточке (по убыванию точности).
CARD_DATE_FIELDS: dict[str, tuple[str, ...]] = {
    "application_start": (
        "дата и время начала срока подачи заявок",
        "дата начала срока подачи заявок",
    ),
    "application_end": (
        "дата и время окончания срока подачи заявок",
        "дата окончания срока подачи заявок",
    ),
    "result_date": ("дата подведения итогов",),
    "auction_date": (
        "дата проведения аукциона",
        "дата проведения процедуры подачи предложений о цене",
        "дата проведения процедуры подачи предложений о сумме цен",
        "дата начала срока подачи ценовых предложений",
        "дата проведения сопоставления ценовых предложений",
    ),
    # Переторжка по 223-ФЗ в ЕИС называется подачей дополнительных ценовых предложений.
    "rebid_date": (
        "дата начала срока подачи дополнительных ценовых предложений",
        "дата проведения сопоставления дополнительных ценовых предложений",
    ),
}
_CARD_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
_CARD_TZ_RE = re.compile(r"МСК\s*([+-]\s*\d{1,2})?")

# Поля тендера, которые карточка уточняет (`refine_deadlines`).
DEADLINE_FIELDS = ("application_start", "application_end")


def parse_card_datetime(value: str) -> datetime | None:
    """«29.09.2026 09:00 (МСК+4)» → 29.09.2026 09:00 UTC+7. Без пометки — московское время;
    без часов — начало того же дня по Москве (ЕИС для таких полей время не указывает)."""

    match = _CARD_DATE_RE.search(value or "")
    if match is None:
        return None
    day, month, year, hour, minute = match.groups()
    if hour is None:
        # Без часов важен календарный день: полночь по поясу заказчика (МСК+4) в Москве
        # оказалась бы вечером предыдущего дня.
        try:
            return datetime(int(year), int(month), int(day), tzinfo=MSK)
        except ValueError:
            return None
    tz_match = _CARD_TZ_RE.search(value)
    shift = int(tz_match.group(1).replace(" ", "")) if tz_match and tz_match.group(1) else 0
    tz = timezone(timedelta(hours=3 + shift)) if shift else MSK
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=tz)
    except ValueError:
        return None


def _has_time(value: str) -> bool:
    match = _CARD_DATE_RE.search(value or "")
    return bool(match and match.group(4) is not None)


def _fields(payload: dict | None) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for section in (payload or {}).get("sections", []) or []:
        for field in section.get("fields", []) or []:
            if isinstance(field, (list, tuple)) and len(field) >= 2:
                fields.append((str(field[0]).strip().lower(), str(field[1])))
    return fields


def _payload_dates(payload: dict | None) -> dict[str, tuple[datetime, bool]]:
    """Ключ пакета → (дата, указано ли время)."""

    fields = _fields(payload)
    found: dict[str, tuple[datetime, bool]] = {}
    for key, prefixes in CARD_DATE_FIELDS.items():
        for prefix in prefixes:
            hit = next(
                (
                    (parsed, _has_time(text))
                    for name, text in fields
                    if name.startswith(prefix) and (parsed := parse_card_datetime(text))
                ),
                None,
            )
            if hit is not None:
                found[key] = hit
                break
    return found


def dates_from_payload(payload: dict | None) -> dict[str, datetime]:
    return {key: value for key, (value, _) in _payload_dates(payload).items()}


def card_dates(db: Session, tender: Tender) -> dict[str, datetime]:
    card = db.get(TenderCard, tender.id)
    return dates_from_payload(card.payload) if card is not None else {}


def precise_deadlines(payload: dict | None) -> dict[str, datetime]:
    """Сроки подачи из карточки, у которых указано время (в поясе заказчика)."""

    return {
        key: value
        for key, (value, with_time) in _payload_dates(payload).items()
        if key in DEADLINE_FIELDS and with_time
    }


def _local_day(value: datetime):
    """Календарный день значения в его собственном поясе — без перевода в Москву: Госплан
    отдаёт местное время заказчика, выдача ЕИС — его дату, и сравнивать их с карточкой надо
    по тем же «настенным» часам."""

    return value.date()


def same_deadline(card_value: datetime, other: datetime) -> bool:
    """Значение площадки описывает тот же срок, что и карточка, только грубее (тот же день).

    Другой день — срок перенесли (новая редакция извещения), а карточка устарела: тогда
    побеждает свежее значение площадки."""

    return _local_day(card_value) == _local_day(other)


def refine_deadlines(
    db: Session, tender: Tender, payload: dict | None, *, fresh: bool = False
) -> list[str]:
    """Ставит точные сроки подачи из карточки — тендеру и записям той же закупки с других
    площадок (тот же реестровый номер), если их срок приходится на тот же день.

    `fresh` — карточку только что забрали с сайта: тогда срок самого тендера ставится и на
    другой день (продление видно в карточке раньше, чем в выдаче). Сохранённая карточка
    могла устареть — у 32616268723 карточка от 01.09 знала срок 25.08, а выдача уже
    показывала продление до 06.10, — поэтому без `fresh` правило «тот же день» для всех.

    В отличие от прочих полей карточки (заполняются, только если пусты), сроки уточняются
    поверх имеющихся: карточка точнее любого другого источника. Дата без времени ставится,
    только если поля не было вовсе. Возвращает описания изменений для истории тендера."""

    with_time = precise_deadlines(payload)
    date_only = {
        key: value
        for key, value in dates_from_payload(payload).items()
        if key in DEADLINE_FIELDS and key not in with_time
    }
    records = [tender]
    if tender.registry_number:
        records += [
            twin
            for twin in db.scalars(
                select(Tender).where(
                    Tender.registry_number == tender.registry_number, Tender.id != tender.id
                )
            )
        ]
    changes: list[str] = []
    for record in records:
        for key in DEADLINE_FIELDS:
            current = getattr(record, key)
            value = with_time.get(key)
            if value is not None:
                if current is not None and not (fresh and record is tender) and not same_deadline(value, current):
                    continue
                value = value.astimezone(MSK)
                if current is not None and current == value:
                    continue
            else:
                value = date_only.get(key)
                if value is None or current is not None:
                    continue
            setattr(record, key, value)
            if record is tender:
                label = "начало подачи заявок" if key == "application_start" else "окончание подачи заявок"
                changes.append(f"{label} — {value:%d.%m.%Y %H:%M} МСК")
    return changes


def keep_card_deadline(db: Session, tender: Tender, field: str, new_value: datetime) -> bool:
    """Не даёт повторному опросу площадки затереть точный срок из карточки более грубым
    значением того же дня (`tender_service._upsert_tender`). Карточка берётся своя или любой
    записи той же закупки: её открывают у одной записи, а уточнение расходится на все."""

    if field not in DEADLINE_FIELDS:
        return False
    condition = TenderCard.tender_id == tender.id
    if tender.registry_number:
        condition = condition | (Tender.registry_number == tender.registry_number)
    payloads = db.scalars(
        select(TenderCard.payload)
        .join(Tender, Tender.id == TenderCard.tender_id)
        .where(condition)
        .order_by(TenderCard.fetched_at.desc())
    )
    for payload in payloads:
        card_value = precise_deadlines(payload).get(field)
        if card_value is not None:
            return same_deadline(card_value, new_value)
    return False
