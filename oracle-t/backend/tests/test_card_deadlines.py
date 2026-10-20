"""Точные сроки подачи заявок из карточки ЕИС (20.10.2026, раздел 68 ARCHITECTURE.md).

Случай из жизни — 32616389457: в карточке «29.09.2026 09:00 (МСК+4)», то есть 05:00 МСК, а в
базе лежало 12:00 МСК — Госплан отдаёт местное время заказчика без пояса, адаптер считал его
UTC; выдача ЕИС той же закупки даёт только дату.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapters.base import TenderSummary
from app.adapters.gosplan import GosplanAdapter
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.services import eis_card_dates
from app.services.eis_card_dates import MSK, parse_card_datetime
from app.services.tender_card_service import fill_gaps_from_card
from app.services.tender_service import _upsert_tender

PLUS4 = timezone(timedelta(hours=7))


def _payload(end: str, start: str = "18.09.2026 (МСК+4)") -> dict:
    return {
        "sections": [
            {
                "title": "Порядок проведения процедуры",
                "fields": [
                    ["Дата начала срока подачи заявок", start],
                    ["Дата и время окончания срока подачи заявок (по местному времени заказчика)", end],
                ],
            }
        ]
    }


def _number() -> str:
    return f"326{uuid.uuid4().int % 10**8:08d}"


def _source(db, key: str) -> Source:
    return db.scalar(select(Source).where(Source.key == key))


def test_parse_card_datetime_formats():
    assert parse_card_datetime("29.09.2026 09:00 (МСК+4)") == datetime(2026, 9, 29, 5, 0, tzinfo=MSK)
    assert parse_card_datetime("01.04.2025 08:55 (МСК)") == datetime(2025, 4, 1, 8, 55, tzinfo=MSK)
    assert parse_card_datetime("10.06.2026 08:50 (МСК-1)") == datetime(2026, 6, 10, 9, 50, tzinfo=MSK)
    # Без времени — полночь того же дня по Москве, а не вечер предыдущего.
    assert parse_card_datetime("18.09.2026 (МСК+4)") == datetime(2026, 9, 18, tzinfo=MSK)
    assert parse_card_datetime("не указано") is None


def test_precise_deadlines_skip_dates_without_time():
    deadlines = eis_card_dates.precise_deadlines(_payload("29.09.2026 09:00 (МСК+4)"))
    assert deadlines == {"application_end": datetime(2026, 9, 29, 9, 0, tzinfo=PLUS4)}


def test_gosplan_223_deadline_is_customer_wall_clock_not_utc():
    item = {"purchase_number": "32616389457", "submission_close_at": "2026-09-29T09:00:00"}
    summary = GosplanAdapter()._summary("fz223", item)
    assert summary.application_end == datetime(2026, 9, 29, 9, 0, tzinfo=MSK)


def test_card_refines_deadline_of_tender_and_its_twins(db_session):
    """Карточка ставит точный срок и своей записи (ЕИС), и записи Госплана той же закупки."""

    number = _number()
    eis, gosplan = _source(db_session, "eis"), _source(db_session, "gosplan")
    _upsert_tender(
        db_session,
        eis,
        TenderSummary(
            external_id=number,
            registry_number=number,
            title="Поставка ПУ",
            source_url="https://zakupki.gov.ru/x",
            application_end=datetime(2026, 9, 29, tzinfo=MSK),
        ),
        groups=[],
    )
    _upsert_tender(
        db_session,
        gosplan,
        GosplanAdapter()._summary(
            "fz223", {"purchase_number": number, "submission_close_at": "2026-09-29T09:00:00"}
        ),
        groups=[],
    )
    db_session.flush()
    records = {
        t.source_id: t for t in db_session.scalars(select(Tender).where(Tender.registry_number == number))
    }
    tender = records[eis.id]

    filled = fill_gaps_from_card(db_session, tender, _payload("29.09.2026 09:00 (МСК+4)"), fresh_card=True)
    db_session.flush()

    expected = datetime(2026, 9, 29, 5, 0, tzinfo=MSK)
    assert tender.application_end == expected
    assert records[gosplan.id].application_end == expected
    # Начало подачи было пустым — ставится и дата без времени.
    assert tender.application_start == datetime(2026, 9, 18, tzinfo=MSK)
    assert any("окончание подачи заявок — 29.09.2026 05:00" in line for line in filled)


def test_stale_card_does_not_undo_extended_deadline(db_session):
    """Сохранённая карточка знала старый срок, а площадка уже показывает продление — без
    свежей карточки срок другого дня не трогаем (32616268723)."""

    number = _number()
    eis = _source(db_session, "eis")
    _upsert_tender(
        db_session,
        eis,
        TenderSummary(
            external_id=number,
            registry_number=number,
            title="Поставка ПУ",
            source_url="https://zakupki.gov.ru/x",
            application_end=datetime(2026, 10, 6, tzinfo=MSK),
        ),
        groups=[],
    )
    db_session.flush()
    tender = db_session.scalar(select(Tender).where(Tender.registry_number == number))

    fill_gaps_from_card(db_session, tender, _payload("25.08.2026 09:00 (МСК)"))
    assert tender.application_end == datetime(2026, 10, 6, tzinfo=MSK)

    # Свежая карточка — источник правды и на другой день.
    fill_gaps_from_card(db_session, tender, _payload("07.10.2026 10:00 (МСК)"), fresh_card=True)
    assert tender.application_end == datetime(2026, 10, 7, 10, 0, tzinfo=MSK)


def test_repoll_keeps_card_deadline_but_takes_extension(db_session):
    """Повторный опрос площадки не затирает точный срок тем же днём без времени, а перенос
    на другой день принимает."""

    number = _number()
    eis = _source(db_session, "eis")

    def summary(day: int) -> TenderSummary:
        return TenderSummary(
            external_id=number,
            registry_number=number,
            title="Поставка ПУ",
            source_url="https://zakupki.gov.ru/x",
            application_end=datetime(2026, 9, day, tzinfo=MSK),
        )

    _upsert_tender(db_session, eis, summary(29), groups=[])
    db_session.flush()
    tender = db_session.scalar(select(Tender).where(Tender.registry_number == number))
    db_session.add(TenderCard(tender_id=tender.id, payload=_payload("29.09.2026 09:00 (МСК+4)")))
    fill_gaps_from_card(db_session, tender, _payload("29.09.2026 09:00 (МСК+4)"), fresh_card=True)
    db_session.flush()

    _upsert_tender(db_session, eis, summary(29), groups=[])
    assert tender.application_end == datetime(2026, 9, 29, 5, 0, tzinfo=MSK)

    _upsert_tender(db_session, eis, summary(30), groups=[])
    assert tender.application_end == datetime(2026, 9, 30, tzinfo=MSK)
