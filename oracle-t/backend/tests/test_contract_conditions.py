"""Условия договора и обеспечения для Bitrix24 (11.11.2026, раздел 95 ARCHITECTURE.md).

Модель не вызывается: ответ подставляется, проверяется то, что делает код вокруг неё —
числа и проценты только из текста, вид дней, «не требуется» только с цитатой, повторы,
комиссия в `deal_fields` только прямо указанной суммой, строки `contract_condition` вместо
коротких «Условий договора».
"""

from __future__ import annotations

import uuid

from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_document import TenderDocument
from app.services import bitrix_ai_service, contract_conditions_service as service
from app.services.contract_conditions_service import ConditionAnswer, ConditionsAnswer

CONTRACT_TEXT = """Проект договора
3.2. Аванс в размере 30 % от цены договора выплачивается в течение 10 рабочих дней.
7.1. За просрочку поставки поставщик уплачивает пеню 0,1 % за каждый день просрочки.
8.1. Обеспечение исполнения договора не требуется.
Прочий текст без отношения к делу.
""" + "\n".join(f"Строка {i}" for i in range(200))


def _tender(db) -> Tender:
    source = Source(key=f"cc_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3262{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков",
        currency="RUB",
        source_url="https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=1",
    )
    db.add(tender)
    db.flush()
    db.add(
        TenderDocument(
            tender_id=tender.id,
            file_name="Проект договора.docx",
            source_url="https://example.test/contract.docx",
            extracted_text=CONTRACT_TEXT,
            document_class="contract",
        )
    )
    db.flush()
    return tender


def _cond(**kw) -> ConditionAnswer:
    base = dict(
        kind="PREPAYMENT", presence="PRESENT", value="30 % от цены договора в течение 10 рабочих дней",
        amount="", percent="30", base="от цены договора", term="в течение 10 рабочих дней", day_type="",
        trigger_event="с даты заключения договора", payer="заказчик", allowed_forms="", obligation_model="",
        lot="1", document="Проект договора.docx", page="п. 3.2", quote="Аванс в размере 30 % от цены договора",
    )
    return ConditionAnswer(**{**base, **kw})


def _answer(*items: ConditionAnswer) -> ConditionsAnswer:
    return ConditionsAnswer(conditions=list(items))


def test_text_for_model_has_condition_fragments(db_session, client):
    tender = _tender(db_session)
    text = service.fragments_text(db_session, tender, 5000, service._CONDITIONS_RE)
    assert "=== Проект договора.docx ===" in text and "Аванс" in text and "пеню" in text
    assert "Строка 150" not in text


def test_parse_number_and_percent():
    assert service.parse_number("1 250 000,50 руб.") == 1250000.5
    assert service.parse_number("150000.00") == 150000
    assert service.parse_number("от 1 до 5 %") is None  # два числа — выбирать нельзя
    assert service.parse_number("") is None
    data = service.normalize(_answer(_cond(percent="130"), _cond(kind="PENALTY", percent="0,1", value="пеня")))
    assert "percent" not in data["conditions"][0]  # 130 % — не процент
    assert data["conditions"][1]["percent"] == 0.1


def test_day_type_from_term_only_when_named():
    data = service.normalize(
        _answer(
            _cond(),
            _cond(kind="WARRANTY", value="24 месяца", term="24 месяца с даты ввода", percent=""),
            _cond(kind="SIGNING_DEADLINE", value="5 дней", term="в течение 5 дней", day_type="calendar", percent=""),
        )
    )
    by_kind = {c["kind"]: c for c in data["conditions"]}
    assert by_kind["PREPAYMENT"]["day_type"] == "WORKING"  # «10 рабочих дней» в самом сроке
    assert "day_type" not in by_kind["WARRANTY"]  # вид дней не назван — не «календарные»
    assert by_kind["SIGNING_DEADLINE"]["day_type"] == "CALENDAR"


def test_not_required_needs_quote_and_unknown_dropped():
    data = service.normalize(
        _answer(
            _cond(kind="CONTRACT_SECURITY", presence="NOT_REQUIRED", value="", percent="", quote="не требуется"),
            _cond(kind="APPLICATION_SECURITY", presence="NOT_REQUIRED", value="", percent="", quote=""),
            _cond(kind="UNKNOWN_KIND"),
            _cond(kind="POSTPAYMENT", value="", term="", percent="", amount=""),  # пустое условие
        )
    )
    assert [c["kind"] for c in data["conditions"]] == ["CONTRACT_SECURITY"]
    assert data["conditions"][0]["presence"] == "NOT_REQUIRED"


def test_single_kinds_deduped_multi_kept_and_order():
    data = service.normalize(
        _answer(
            _cond(kind="PENALTY", value="Пеня за просрочку поставки", percent="0.1"),
            _cond(),
            _cond(value="другая формулировка аванса"),  # второй аванс того же лота — повтор
            _cond(kind="PENALTY", value="Штраф за неисполнение", percent="10"),
            _cond(kind="PREPAYMENT", lot="2", value="аванс по лоту 2"),
            _cond(kind="CUSTOMER_OBLIGATION", value="лимит", obligation_model="limit_only", percent=""),
            _cond(kind="WARRANTY", value="12 месяцев", obligation_model="LIMIT_ONLY", percent=""),
        )
    )
    kinds = [(c["kind"], c["lot_number"]) for c in data["conditions"]]
    assert kinds == [  # порядок вкладки «Договор» — порядок KINDS
        ("WARRANTY", "1"), ("PREPAYMENT", "1"), ("PREPAYMENT", "2"), ("PENALTY", "1"), ("PENALTY", "1"),
        ("CUSTOMER_OBLIGATION", "1"),
    ]
    assert data["conditions"][-1]["obligation_model"] == "LIMIT_ONLY"
    assert "obligation_model" not in data["conditions"][0]  # модель обязательств — только у своего вида


def test_platform_commission_only_explicit_amount():
    tariff = service.normalize(_answer(_cond(kind="PLATFORM_COMMISSION", value="1 % от НМЦК", percent="1", amount="")))
    assert service.platform_commission(tariff["conditions"]) is None  # тариф не пересчитываем
    explicit = service.normalize(_answer(_cond(kind="PLATFORM_COMMISSION", value="2000 руб.", percent="", amount="2 000")))
    assert service.platform_commission(explicit["conditions"]) == 2000


def test_extract_replaces_previous_and_keeps_them_on_failure(db_session, client, monkeypatch):
    tender = _tender(db_session)
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _answer(_cond()))
    assert service.extract(db_session, tender) == {"conditions": 1}

    def boom(*a, **k):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(service, "run_structured", boom)
    try:
        service.extract(db_session, tender)
    except RuntimeError:
        pass
    assert len(service.get_conditions(db_session, tender)["conditions"]) == 1  # прежнее осталось


def test_bitrix_rows_replace_short_contract_terms(db_session, client, monkeypatch):
    tender = _tender(db_session)
    db_session.add(
        TenderCard(
            tender_id=tender.id,
            payload={"extra_sections": {"sections": {"contract_terms": ["Аванс 30 %"], "special_conditions": ["Шефмонтаж"]}}},
        )
    )
    db_session.flush()

    # Без структурного разбора — прежние короткие строки.
    sections = bitrix_ai_service.build_sections(db_session, tender)
    assert [r["title"] for r in sections["contract"] if r["type"] == "contract_condition"] == ["Условие договора 1"]

    monkeypatch.setattr(
        service,
        "run_structured",
        lambda *a, **k: _answer(
            _cond(),
            _cond(kind="PENALTY", value="Пеня 0,1 % за день", percent="0,1"),
            _cond(kind="CONTRACT_SECURITY", presence="NOT_REQUIRED", value="", percent="", quote="не требуется"),
            _cond(kind="APPLICATION_SECURITY", value="1 % НМЦК", percent="1", quote=""),
            _cond(kind="PLATFORM_COMMISSION", value="2000 руб.", percent="", amount="2000"),
        ),
    )
    service.extract(db_session, tender)
    sections = bitrix_ai_service.build_sections(db_session, tender)
    rows = {r["data"]["condition_kind"]: r for r in sections["contract"] if r["type"] == "contract_condition"}
    assert set(rows) == {"PREPAYMENT", "PENALTY", "CONTRACT_SECURITY", "PLATFORM_COMMISSION"}
    assert "Условие договора 1" not in [r["title"] for r in sections["contract"]]  # не дублируется
    assert [r["type"] for r in sections["contract"]].count("obligation") == 1  # особые условия остались

    prepayment = rows["PREPAYMENT"]
    assert prepayment["key"] == "contract-condition-prepayment-lot-1"
    assert prepayment["title"] == "Аванс (предоплата)" and prepayment["status"] == "extracted"
    assert prepayment["data"]["percent"] == 30 and prepayment["data"]["day_type"] == "рабочие"
    assert prepayment["evidence"] == [
        {"document": "Проект договора.docx", "page": "п. 3.2", "quote": "Аванс в размере 30 % от цены договора"}
    ]
    assert rows["PENALTY"]["status"] == "requires_review"  # санкции — всегда на проверку
    assert rows["CONTRACT_SECURITY"]["data"]["condition"].startswith("Не требуется")

    (security,) = [r for r in sections["preparation"] if r["title"] == "Обеспечение заявки"]
    assert security["type"] == "checklist" and security["status"] == "requires_review"  # без цитаты

    assert bitrix_ai_service.build_deal_fields(db_session, tender)["platform_commission"] == 2000

    again = bitrix_ai_service.build_sections(db_session, tender)
    assert [r["key"] for r in again["contract"]] == [r["key"] for r in sections["contract"]]


def test_contract_conditions_api(client, admin_token):
    from app.db.session import SessionLocal

    headers = {"Authorization": f"Bearer {admin_token}"}
    with SessionLocal() as db:
        tender = _tender(db)
        db.commit()
        tender_id = str(tender.id)
    response = client.get(f"/tenders/{tender_id}/contract-conditions", headers=headers)
    assert response.status_code == 200 and response.json() == {"conditions": []}
    assert client.get(f"/tenders/{uuid.uuid4()}/contract-conditions", headers=headers).status_code == 404
