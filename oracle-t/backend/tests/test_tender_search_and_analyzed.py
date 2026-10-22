"""Поиск без различия Е/Ё и организационно-правовой формы, раздел «Разобранные»
(замечания 14.10.2026 и 22.10.2026)."""

from __future__ import annotations

import uuid

from app.models.analysis import Requirement
from app.models.source import Source
from app.models.tender import Tender
from app.services.tender_service import TenderFilters, count_tenders, list_tenders


def _tender(db, title: str, customer: str | None = None) -> Tender:
    source = Source(
        key=f"srch_{uuid.uuid4().hex[:8]}",
        name="Источник для теста поиска",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"SR-{uuid.uuid4().hex[:6]}",
        title=title,
        customer_name=customer,
        currency="RUB",
    )
    db.add(tender)
    db.flush()
    return tender


def _found(db, search: str) -> set[uuid.UUID]:
    return {t.id for t in list_tenders(db, limit=1000, filters=TenderFilters(search=search))}


def test_search_ignores_yo(db_session):
    db = db_session
    marker = uuid.uuid4().hex[:6]
    with_yo = _tender(db_session, f"Поставка счётчиков {marker}")
    without_yo = _tender(db_session, f"Поставка счетчиков {marker}")
    customer = _tender(db_session, f"Закупка {marker}", customer="ООО «Объединённые сети»")

    both = {with_yo.id, without_yo.id}
    assert both <= _found(db_session, f"счетчиков {marker}")
    assert both <= _found(db_session, f"СЧЁТЧИКОВ {marker}")
    assert customer.id in _found(db_session, "объединенные сети")


def test_search_by_customer_ignores_legal_form_and_quotes(db_session):
    """Замечание 22.10.2026: «АКЦИОНЕРНОЕ ОБЩЕСТВО "ЭНЕРГОСБЫТ ПЛЮС"» не находил заказчика,
    записанного как «АО «ЭнергосбыТ Плюс»». Форма и кавычки не важны; номер ищется тоже."""

    marker = uuid.uuid4().hex[:6]
    tender = _tender(db_session, "Поставка приборов учёта", customer=f"АО «ЭнергосбыТ Плюс {marker}»")

    assert tender.id in _found(db_session, f'АКЦИОНЕРНОЕ ОБЩЕСТВО "ЭНЕРГОСБЫТ ПЛЮС {marker}"')
    assert tender.id in _found(db_session, f"энергосбыт {marker}")
    assert tender.id in _found(db_session, tender.external_id)
    assert tender.id not in _found(db_session, f"энергосбыт {marker} тюмень")


def test_analyzed_filter_counts_only_tenders_with_requirements(db_session):
    db = db_session
    marker = uuid.uuid4().hex[:6]
    analyzed = _tender(db_session, f"Разобранная {marker}")
    _tender(db_session, f"Неразобранная {marker}")
    db.add(Requirement(tender_id=analyzed.id, text="Класс точности 1"))
    db.flush()

    assert count_tenders(db_session, filters=TenderFilters(search=marker)) == 2
    only = list_tenders(db_session, filters=TenderFilters(search=marker, analyzed=True))
    assert [t.id for t in only] == [analyzed.id]
