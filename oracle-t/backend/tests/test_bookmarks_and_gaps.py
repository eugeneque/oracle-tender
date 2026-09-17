"""Избранное и дозаполнение полей для фильтров (замечания тестировщика 16.09.2026).

Избранное показывается вне фильтров по умолчанию: отложенная закупка не должна пропасть
из раздела оттого, что у неё истёк срок подачи. Дозаполнение — то, из-за чего «не
работали» фильтры: ОКПД2 и регион берутся из уже сохранённых карточек без сети, тип
конкурса — из наименования.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender, TenderType
from app.models.tender_card import TenderCard
from app.services import tender_gaps_service
from app.services.tender_gaps_service import tender_type_from_title


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_tender(db, *, title: str, expired: bool = False, **fields) -> Tender:
    source = Source(
        key=f"bm_{uuid.uuid4().hex[:8]}", name="Площадка", url="https://example.test", type="eis"
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"BM-{uuid.uuid4().hex[:8]}",
        title=title,
        status="collecting_bids",
        currency="RUB",
        source_url="https://example.test/t",
        application_end=datetime.now(timezone.utc) + timedelta(days=-3 if expired else 3),
        **fields,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return tender


# ------------------------------------------------------------------------------ избранное


def test_bookmark_survives_default_filters_and_is_personal(client, admin_token):
    db = SessionLocal()
    try:
        tender = _make_tender(db, title="Поставка счётчиков", expired=True, passed_relevance_filter=False)
    finally:
        db.close()

    # Просроченная и не прошедшая профиль — под фильтрами по умолчанию её нет.
    hidden = client.get(
        "/tenders?hide_expired=true&only_profile_relevant=true&limit=1000", headers=_auth(admin_token)
    ).json()
    assert str(tender.id) not in {row["id"] for row in hidden["items"]}

    added = client.put(
        f"/tenders/{tender.id}/bookmark", json={"note": "Посмотреть после отпуска"}, headers=_auth(admin_token)
    )
    assert added.status_code == 200, added.text
    assert added.json()["is_bookmarked"] is True

    note = client.get(f"/tenders/{tender.id}/bookmark", headers=_auth(admin_token)).json()
    assert note["note"] == "Посмотреть после отпуска"

    # В избранном она есть — независимо от срока и профиля.
    favourites = client.get("/tenders?bookmarked=true&limit=1000", headers=_auth(admin_token)).json()
    row = next(r for r in favourites["items"] if r["id"] == str(tender.id))
    assert row["is_bookmarked"] is True

    # А обычная карточка знает, что закупка в избранном.
    assert client.get(f"/tenders/{tender.id}", headers=_auth(admin_token)).json()["is_bookmarked"] is True

    removed = client.delete(f"/tenders/{tender.id}/bookmark", headers=_auth(admin_token))
    assert removed.status_code == 200 and removed.json()["is_bookmarked"] is False
    assert client.get(f"/tenders/{tender.id}/bookmark", headers=_auth(admin_token)).json() is None
    favourites = client.get("/tenders?bookmarked=true&limit=1000", headers=_auth(admin_token)).json()
    assert str(tender.id) not in {r["id"] for r in favourites["items"]}


# ---------------------------------------------------------------------- дозаполнение


def test_tender_type_from_title_follows_appendix_e():
    assert tender_type_from_title("Поставка счетчиков электрической энергии") == TenderType.SUPPLY_ONLY.value
    assert tender_type_from_title("Поставка и монтаж приборов учёта") == TenderType.COMPLEX.value
    assert tender_type_from_title("Выполнение работ по замене приборов учета") == TenderType.WORKS_ONLY.value
    assert tender_type_from_title("Поверка счетчиков электроэнергии") == TenderType.REVERIFICATION.value
    # Без ключевых слов — не «прочее», а «не знаем»: «прочее» тоже вердикт.
    assert tender_type_from_title("Оказание услуг по предоставлению информации ИСУ") is None
    assert tender_type_from_title(None) is None


def test_backfill_from_stored_card_fills_okpd2_and_type(db_session):
    """Карточка уже лежит в базе — ОКПД2 из таблицы лотов и тип по наименованию
    заполняются без единого обращения к сайту."""

    tender = _make_tender(db_session, title="Поставка счетчиков электрической энергии")
    db_session.add(
        TenderCard(
            tender_id=tender.id,
            payload={
                "sections": [],
                "tables": {
                    "lots": {
                        "title": "Список лотов",
                        "headers": ["Номер, наименование лота", "Классификация по ОКПД2"],
                        "rows": [["1 Поставка счетчиков", "26.51.63.130 Счетчики электроэнергии"]],
                    }
                },
                "tab_urls": {},
            },
        )
    )
    db_session.commit()

    outcome = tender_gaps_service.backfill_from_stored_cards(db_session)

    db_session.refresh(tender)
    assert tender.okpd2_code == "26.51.63.130"
    assert tender.tender_type == TenderType.SUPPLY_ONLY.value
    assert outcome.okpd2_filled >= 1

    # Повторный проход ничего не трогает: значение уже стоит.
    again = tender_gaps_service.backfill_from_stored_cards(db_session)
    db_session.refresh(tender)
    assert tender.okpd2_code == "26.51.63.130"
    assert again.okpd2_filled == 0


def test_new_tender_gets_type_from_title_on_collect(db_session):
    from app.adapters.base import TenderSummary
    from app.services.tender_service import _upsert_tender

    source = Source(key=f"gap_{uuid.uuid4().hex[:8]}", name="П", url="https://e.test", type="eis")
    db_session.add(source)
    db_session.flush()
    summary = TenderSummary(
        external_id=f"G-{uuid.uuid4().hex[:6]}",
        title="Поверка средств измерений",
        source_url="https://e.test/1",
    )
    assert _upsert_tender(db_session, source, summary, groups=[]) is True
    tender = db_session.query(Tender).filter(Tender.external_id == summary.external_id).one()
    assert tender.tender_type == TenderType.REVERIFICATION.value
