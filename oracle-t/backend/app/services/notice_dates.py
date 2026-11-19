"""Дата аукциона и переторжки из текста документации — кодом (19.11.2026, раздел 115).

Перечень Bitrix24, пп. 2–3: «Назначены ли дата и время аукциона / переторжки?». Карточка ЕИС
даёт их точно, но не всегда (коммерческие площадки, 223-ФЗ без отдельного поля), а в
документации они записаны оборотами: «Дата проведения переторжки: 02.10.2026 г. в 13:00 (время
местное)», «не позднее «15» октября 2026 года», «Дата и время проведения переторжки указываются
в протоколе».

Правила перечня: неизвестный часовой пояс не угадывается («время местное» — пояс заказчика,
но какой — в тексте нет), поэтому в поле сделки уходит только дата со временем и известным
поясом; остальное — строкой с видом сведения: точная дата, «не позднее», «определяется
протоколом / площадкой», и с противоречием, если карточка ЕИС говорит другое.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
_MONTHS = {m: i for i, m in enumerate(
    ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"], start=1)}

_ANCHORS = {
    "auction": re.compile(
        r"дат\w*\s+(?:и\s+время\s+)?(?:проведения\s+(?:электронного\s+)?аукциона|проведения\s+торгов|"
        r"(?:начала\s+)?подачи\s+ценовых\s+предложений|проведения\s+процедуры\s+подачи\s+предложений\s+о\s+цене)",
        re.I,
    ),
    "rebid": re.compile(r"дат\w*\s+(?:и\s+время\s+)?(?:проведения\s+)?(?:[а-яё]+\s+){0,2}переторжк\w*", re.I),
}
_NUM_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_WORD_DATE = re.compile(r"«?(\d{1,2})»?\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\s+(\d{4})", re.I)
_TIME = re.compile(r"(\d{1,2})[:.](\d{2})")
_TZ_MSK = re.compile(r"МСК\s*([+-]\s*\d{1,2})?|московск", re.I)
_LOCAL = re.compile(r"местн", re.I)
_NOT_LATER = re.compile(r"не\s+позднее", re.I)
_DEFERRED = re.compile(r"указыва\w+\s+в\s+протокол|в\s+протоколе|на\s+(?:сайте\s+)?(?:электронной\s+)?площадк|средствами\s+(?:ЭТП|ЭП|площадки)|будет\s+(?:сообщен|определен|назначен)|решени\w+\s+о\s+проведении", re.I)


@dataclass
class FoundDate:
    kind: str  # exact | not_later | deferred
    day: date | None
    time: str | None
    tz_known: bool
    moment: datetime | None
    quote: str
    document: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "date": self.day.isoformat() if self.day else None,
            "time": self.time,
            "tz_known": self.tz_known,
            "moment": self.moment.isoformat(timespec="seconds") if self.moment else None,
            "quote": self.quote,
            "document": self.document,
        }


def _day(window: str) -> tuple[date | None, int]:
    found: list[tuple[int, date]] = []
    if m := _NUM_DATE.search(window):
        try:
            found.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    if m := _WORD_DATE.search(window):
        stem = m.group(2).lower()
        month = next((n for s, n in _MONTHS.items() if stem.startswith(s)), None)
        if month:
            try:
                found.append((m.start(), date(int(m.group(3)), month, int(m.group(1)))))
            except ValueError:
                pass
    if not found:
        return None, -1
    position, value = min(found)
    return value, position


def find_in_text(text: str, document: str | None = None) -> dict[str, FoundDate]:
    out: dict[str, FoundDate] = {}
    for key, anchor in _ANCHORS.items():
        for match in anchor.finditer(text or ""):
            window = text[match.end(): match.end() + 220]
            sentence = window.split("\n")[0] if _day(window.split("\n")[0])[0] else window
            day, position = _day(sentence)
            # «…от 05.04.2013 № 44-ФЗ» — ссылка на документ, а не дата торгов.
            if day is not None and re.search(r"\bот\s*«?$", sentence[:position]):
                day = None
            quote = " ".join((match.group(0) + sentence[: max(position, 0) + 60]).split())[:300]
            if day is None:
                if _DEFERRED.search(window[:200]) and key not in out:
                    out[key] = FoundDate("deferred", None, None, False, None, " ".join((match.group(0) + window[:160]).split()), document)
                continue
            before = sentence[:position]
            tail = sentence[position: position + 60]
            time_match = _TIME.search(tail[10:]) if len(tail) > 10 else None
            hhmm = f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else None
            tz_match = _TZ_MSK.search(sentence[: position + 80])
            local = bool(_LOCAL.search(sentence[: position + 80]))
            moment = None
            if hhmm and tz_match and not local:
                shift = tz_match.group(1)
                tz = timezone(timedelta(hours=3 + int(shift.replace(" ", "")))) if shift else MSK
                hour, minute = (int(x) for x in hhmm.split(":"))
                moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            found = FoundDate(
                "not_later" if _NOT_LATER.search(before) else "exact",
                day, hhmm, moment is not None, moment, quote, document,
            )
            # Точная дата главнее «не позднее» и «определяется протоколом».
            if key not in out or (out[key].kind != "exact" and found.kind == "exact"):
                out[key] = found
            break
    return out


def from_documents(documents: list[tuple[str, str]]) -> dict[str, FoundDate]:
    """По документам в порядке приоритета (извещение, документация): первое найденное."""

    out: dict[str, FoundDate] = {}
    for name, text in documents:
        for key, value in find_in_text(text, name).items():
            if key not in out or (out[key].kind != "exact" and value.kind == "exact"):
                out[key] = value
    return out


def for_tender(db: Any, tender: Any) -> dict[str, FoundDate]:
    from sqlalchemy import select

    from app.models.tender_document import DocumentClass, TenderDocument
    from app.services import tender_twins

    subject = tender_twins.subject(db, tender)
    docs = db.scalars(
        select(TenderDocument).where(
            TenderDocument.tender_id.in_(list(dict.fromkeys((tender.id, subject.id)))),
            TenderDocument.extracted_text.is_not(None),
            TenderDocument.document_class != DocumentClass.PROTOCOL.value,
        )
    ).all()
    docs = sorted(docs, key=lambda d: (not d.is_priority_source, "извещ" not in d.file_name.lower(), d.created_at))
    found = from_documents([(d.file_name, d.extracted_text or "") for d in docs])
    # Дата раньше публикации закупки — чужая (прошлый период, ссылка на документ).
    published = tender.publish_date or (tender.created_at.date() if tender.created_at else None)
    return {k: v for k, v in found.items() if v.day is None or published is None or v.day >= published - timedelta(days=1)}
