"""НМЦК с НДС и без НДС из документации (раздел 106 ARCHITECTURE.md).

Формулировки взяты из реальных извещений и документаций (скриншоты запроса 12.11.2026 и
закупки локальной базы): разбор кодом, модель не участвует.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services import price_vat_service as service
from app.services.price_vat_service import complete, parse_block


def _parse(text: str) -> dict | None:
    match = service._ANCHOR_RE.search(text)
    found = parse_block(" ".join(text[match.start():].split()))
    return complete(found) if found else None


def test_price_with_vat_amount_and_rate():
    record = _parse(
        "1.1.7. Сведения о начальной (максимальной) цене договора: начальная (максимальная) цена "
        "договора составляет 2 187 987,55 руб. (Два миллиона сто восемьдесят семь тысяч девятьсот "
        "восемьдесят семь рублей пятьдесят пять копеек), в т.ч. НДС 22% 394 555,13 руб."
    )
    assert record["mode"] == "included"
    assert record["price_with_vat"] == "2187987.55"
    assert record["price_without_vat"] == "1793432.42"
    assert record["vat_rate"] == "22" and record["vat_amount"] == "394555.13"
    assert "computed" not in record  # обе цены — из текста


def test_price_without_vat_computes_with_vat_by_default_rate():
    record = _parse("5. Начальная (максимальная) цена договора: 1 729 350,07 руб. без НДС.")
    assert record["mode"] == "excluded"
    assert record["price_without_vat"] == "1729350.07"
    assert record["price_with_vat"] == "2109807.09"
    assert record["computed"] == "with_vat" and record["rate_assumed"] is True


def test_not_applicable_wins_over_bez_nds_at_same_place():
    record = _parse("Начальная (максимальная) цена договора: 500 000,00 руб. без НДС (НДС не облагается).")
    assert record["mode"] == "not_applicable"
    assert record["price_with_vat"] == record["price_without_vat"] == "500000.00"


def test_vat_on_top_and_second_price_stated():
    record = _parse(
        "Начальная (максимальная) цена договора составляет 33 539 622,95 руб., кроме того, "
        "НДС в размере 22% 7 378 717,05 руб."
    )
    assert record["mode"] == "excluded"
    assert record["price_with_vat"] == "40918340.00"
    assert record["vat_rate"] == "22" and "computed" not in record

    record = _parse(
        "Начальная максимальная цена контракта 1 000 000,00 руб. без учета НДС, "
        "1 220 000,00 руб. с учетом НДС"
    )
    assert record["price_with_vat"] == "1220000.00" and record["vat_rate"] == "22"


def test_kopecks_written_after_amount_in_words():
    record = _parse(
        "Начальная (максимальная) цена договора составляет: 4 591 578 (Четыре миллиона пятьсот "
        "девяносто одна тысяча пятьсот семьдесят восемь) рублей 49 копеек с НДС."
    )
    assert record["price_with_vat"] == "4591578.49"
    assert record["computed"] == "without_vat"


def test_vat_word_in_later_clause_is_ignored():
    """«Без НДС» о сравнении предложений через несколько сотен знаков — не про эту цену."""

    text = "НМЦК: 1 000 000,00 руб. " + "Прочие условия процедуры. " * 30 + "Предложения сравниваются без НДС."
    assert _parse(text) is None


@pytest.mark.parametrize("text", ["Начальная (максимальная) цена: ___ руб. в 2027 (две тысячи) году с НДС"])
def test_year_is_not_a_price(text):
    assert _parse(text) is None


def _tender(db, price: str | None, docs: list[tuple[str, str, str]]) -> Tender:
    source = Source(key=f"vat_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3262{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков",
        currency="RUB",
        price=Decimal(price) if price else None,
    )
    db.add(tender)
    db.flush()
    for name, cls, text in docs:
        db.add(
            TenderDocument(
                tender_id=tender.id,
                file_name=name,
                source_url=f"https://example.test/{name}",
                extracted_text=text,
                document_class=cls,
            )
        )
    db.flush()
    return tender


def test_refresh_prefers_place_matching_platform_price(db_session):
    tender = _tender(
        db_session,
        "1729350.07",
        [
            ("Документация.docx", "other", "Начальная (максимальная) цена лота 2: 99 000,00 руб. с НДС"),
            ("Извещение.docx", "notice", "5. Начальная (максимальная) цена договора: 1 729 350,07 руб. без НДС."),
        ],
    )
    record = service.refresh(db_session, tender)
    assert record["mode"] == "excluded" and record["document"] == "Извещение.docx"
    assert record["matches_platform_price"] is True
    assert tender.price_vat == record


def test_weak_anchor_only_with_matching_platform_price(db_session):
    text = "Цена договора не больше 891 975,43 рублей без НДС."
    tender = _tender(db_session, "891975.43", [("Извещение.docx", "notice", text)])
    assert service.refresh(db_session, tender)["price_without_vat"] == "891975.43"

    other = _tender(db_session, "5000000", [("Проект договора.docx", "contract", text)])
    assert service.refresh(db_session, other) is None
    assert other.price_vat is None


def test_nothing_found_keeps_previous_value(db_session):
    tender = _tender(db_session, None, [("ТЗ.docx", "tz_description", "Счётчики однофазные")])
    tender.price_vat = {"mode": "included", "price_with_vat": "1.00"}
    assert service.refresh(db_session, tender) is None
    assert tender.price_vat == {"mode": "included", "price_with_vat": "1.00"}


# --- блок `pricing` для Bitrix24 (API_CHANGE 17.11.2026) ----------------------------------------


def _tender_with(record, currency="RUB"):
    from types import SimpleNamespace

    return SimpleNamespace(price_vat=record, currency=currency)


def test_pricing_block_from_documents():
    from app.services.bitrix_ai_service import build_pricing

    record = {
        "mode": "included", "doc_price": "100.00", "vat_rate": "22", "rate_assumed": False,
        "document": "Извещение.docx",
        "quote": "Начальная (максимальная) цена лота — 100 рублей, включая НДС 22%",
    }
    assert build_pricing(_tender_with(record)) == {
        "source_amount": "100.00", "amount_kind": "LOT_MAX", "vat_basis": "INCLUDED",
        "vat_rate": 22, "source_document": "Извещение.docx", "source_text": record["quote"],
    }


def test_pricing_does_not_invent_values():
    from app.services.bitrix_ai_service import build_pricing

    base = {"mode": "excluded", "doc_price": "100.00", "vat_rate": "22", "rate_assumed": True,
            "quote": "НМЦК 100 руб. без НДС"}
    assert build_pricing(_tender_with(base)) is None  # вид цены не определился
    ok = dict(base, quote="Максимальное значение цены договора 100 руб. без НДС")
    block = build_pricing(_tender_with(ok))
    # П. 96: если ставка не названа, для расчёта и пакета используется 22 %.
    assert block["amount_kind"] == "CONTRACT_MAX" and block["vat_rate"] == 22
    assert build_pricing(_tender_with(ok, currency="USD")) is None
    assert build_pricing(_tender_with(None)) is None


def test_vat22_deal_amount_rules():
    base = {"mode": "excluded", "doc_price": "100.00", "quote": "Максимальная цена договора 100 руб. без НДС"}
    calculated = service.vat22(_tender_with(base))
    assert calculated["eligible"] is True and calculated["amount_with_vat_22"] == "122.00"
    assert service.deal_amount(_tender_with(base)) == Decimal("122.00")
    assert service.deal_amount(_tender_with(base), won=True) is None
    assert service.deal_amount(_tender_with(dict(base, quote="НМЦК 100 руб. без НДС"))) is None  # назначение неизвестно

    included = service.vat22(_tender_with(dict(base, mode="included")))
    assert included["eligible"] is True and included["amount_with_vat_22"] == "100.00"

    for record in (
        dict(base, vat_rate="20"),
        dict(base, mode="not_applicable"),
        dict(base, conflicts=[{"doc_price": "120.00"}]),
        dict(base, matches_platform_price=False),
    ):
        result = service.vat22(_tender_with(record))
        assert result["eligible"] is False and result["amount_with_vat_22"] is None
        assert result["review_reason"]
        assert service.deal_amount(_tender_with(record)) is None


def test_lot_records_and_pricing():
    tender = _tender_with({
        "mode": "excluded", "doc_price": "1000.00", "vat_rate": "22",
        "quote": "НМЦК лота 1 — 1000 руб. без НДС",
        "lots": {
            "1": {"mode": "excluded", "doc_price": "1000.00", "quote": "НМЦК лота 1 — 1000 руб. без НДС"},
            "2": {"mode": "included", "doc_price": "2000.00", "quote": "НМЦК лота 2 — 2000 руб. с НДС"},
        },
    })
    from app.services.bitrix_ai_service import build_pricing

    assert service.record_for_lot(tender) is None  # запись лота не подменяет цену процедуры
    assert service.record_for_lot(tender, "1")["doc_price"] == "1000.00"
    assert service.deal_amount(tender, "1") == Decimal("1220.00")
    assert service.deal_amount(tender, "2") == Decimal("2000.00")
    assert build_pricing(tender, {"lot_number": "2"})["source_amount"] == "2000.00"


def test_refresh_collects_lot_prices_and_conflicts(db_session):
    tender = _tender(
        db_session,
        "3000.00",
        [("Извещение.docx", "notice", "НМЦК лота 1: 1 000,00 руб. без НДС\nНМЦК лота 2: 2 000,00 руб. с НДС")],
    )
    from app.models.tender_card import TenderCard

    db_session.add(TenderCard(tender_id=tender.id, payload={"tables": {"lots": {"headers": [], "rows": [
        ["1 Первый", "Нет", "Цена: 1 000,00 ₽"], ["2 Второй", "Нет", "Цена: 2 000,00 ₽"],
    ]}}}))
    db_session.flush()
    record = service.refresh(db_session, tender)
    assert record["lots"]["1"]["doc_price"] == "1000.00"
    assert record["lots"]["2"]["doc_price"] == "2000.00"
    assert service.deal_amount(tender, "1") == Decimal("1220.00")
    assert service.deal_amount(tender, "2") == Decimal("2000.00")

    doc = db_session.query(TenderDocument).filter_by(tender_id=tender.id).one()
    doc.extracted_text += "\nНМЦК лота 2: 2 100,00 руб. с НДС"
    db_session.flush()
    record = service.refresh(db_session, tender)
    assert record["lots"]["2"]["conflicts"]
    assert service.deal_amount(tender, "2") is None
