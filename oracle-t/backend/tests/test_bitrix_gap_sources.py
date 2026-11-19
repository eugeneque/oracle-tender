"""Оставшиеся пункты перечня Bitrix24 (19.11.2026, раздел 115 ARCHITECTURE.md).

Сеть не используется: страницы реестров ЕИС — сохранённые фрагменты разметки, сведения
реестров по закупке подставляются в `tender_eis_registry`.
"""

from __future__ import annotations

import uuid
from datetime import date

from bs4 import BeautifulSoup

from app.adapters import eis_registries
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_eis_registry import TenderEisRegistry
from app.services import bitrix_ai_service, bitrix_extra_rows, notice_dates
from app.services.tender_monitor_service import diff_snapshots

COMPLAINT_BLOCK = """<div class="search-registry-entry-block">44-ФЗ № 201700132489003524 Рассмотрена Признана обоснованной
Предписание не выдано Орган контроля УФАС ПО Г. МОСКВЕ Субъект контроля Заказчик ГБУ «Жилищник»
Лицо, подавшее жалобу ООО «Профит плюс» Жалоба Поступление жалобы 15.03.2017 Размещено 17.03.2017
Извещение № 0373200086717000012 Решение № 2-57-2754/77-17 от 29.03.2017
<a href="/epz/complaint/card/general-information.html?complaintNumber=201700132489003524">x</a></div>"""

CONTRACT_CARD = """<section><span class="section__title">Полное наименование заказчика</span><span class="section__info">АДМИНИСТРАЦИЯ</span></section>
<section><span class="section__title">Полное наименование</span><span class="section__info">АДМИНИСТРАЦИЯ</span></section>
<section><span class="section__title">Цена контракта</span><span class="section__info">1 000,00 ₽</span></section>
<section><span class="section__title">Полное наименование</span><span class="section__info">ООО «МИРТЕК»</span></section>
<section><span class="section__title">ИНН</span><span class="section__info">6154125635</span></section>"""


def test_complaint_entry_parsed():
    block = BeautifulSoup(COMPLAINT_BLOCK, "lxml").select_one(".search-registry-entry-block")
    entry = eis_registries.parse_complaint_entry(block)
    assert entry["number"] == "201700132489003524" and entry["status"] == "Рассмотрена"
    assert entry["outcome"] == "Признана обоснованной" and entry["prescription"] is False
    assert entry["applicant"] == "ООО «Профит плюс»" and entry["decision_number"] == "2-57-2754/77-17"
    assert entry["received"] == "2017-03-15"


def test_contract_supplier_is_not_customer():
    soup = BeautifulSoup(CONTRACT_CARD, "lxml")
    assert eis_registries.supplier_of(eis_registries.pairs(soup)) == ("ООО «МИРТЕК»", "6154125635")


def test_notice_dates_kinds_and_timezone():
    exact = notice_dates.find_in_text("Дата и время проведения аукциона: 21.11.2026 10:00 (МСК+4)")["auction"]
    assert exact.moment.isoformat() == "2026-11-21T10:00:00+07:00"
    local = notice_dates.find_in_text("Дата проведения переторжки: 02.10.2026 г. в 13:00 (время местное).")["rebid"]
    assert local.time == "13:00" and local.moment is None  # пояс не угадывается
    later = notice_dates.find_in_text("Дата проведения этапа переторжки: не позднее «15» октября 2026 года.")["rebid"]
    assert later.kind == "not_later" and later.day == date(2026, 10, 15)
    deferred = notice_dates.find_in_text("Дата и время проведения переторжки указываются в протоколе рассмотрения")["rebid"]
    assert deferred.kind == "deferred"
    assert notice_dates.find_in_text("Дата проведения аукциона определена законом от 05.04.2013 № 44-ФЗ") == {}


def _snap(**extra):
    base = {"fields": {"application_end": None, "price": None, "status": None}, "security": {}, "documents": {}}
    base.update(extra)
    return base


def test_registry_complaint_and_contract_events():
    old = _snap(registry_complaints={}, registry_contracts={"5270": {"version": 1, "price": 100.0, "execution_end": "2026-11-30"}})
    new = _snap(
        registry_complaints={"2026001": {"status": "Рассмотрена", "outcome": "Признана необоснованной", "prescription": False,
                                         "hearing_at": "2026-11-20T11:00+03:00", "applicant": "ООО «Лютик»", "placed": "2026-11-15"}},
        registry_contracts={"5270": {"version": 2, "price": 120.0, "execution_end": "2026-11-30",
                                     "change_reason": "Дополнительное соглашение", "placed": "2026-11-18T10:00+10:00"}},
    )
    changes = diff_snapshots(old, new)
    kinds = sorted(c["data"].get("fas_event") for c in changes if c["data"].get("fas_event"))
    assert kinds == ["COMPLAINT", "DECISION", "HEARING"]
    contract = next(c for c in changes if c["event_type"] == "CONTRACT_CHANGED")
    assert contract["data"]["changes"] == [{"key": "price", "field": "Цена договора", "old_value": 100.0, "new_value": 120.0}]
    assert contract["source_version"] == "2"
    # Реестр ещё не читался в прошлый раз — первое чтение не события.
    assert diff_snapshots(_snap(), new) == []


def _tender(db) -> Tender:
    source = Source(key=f"gs_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(source_id=source.id, external_id="32615974226", registry_number="32615974226",
                    title="Поставка счётчиков", currency="RUB", price=1_000_000)
    db.add(tender)
    db.flush()
    return tender


def test_registry_rows_in_package(db_session):
    tender = _tender(db_session)
    db_session.add(TenderEisRegistry(
        tender_id=tender.id,
        complaints=[{"number": "2026001", "status": "Рассмотрена", "outcome": "Признана необоснованной", "prescription": False,
                     "hearing_at": "2026-11-20T11:00+03:00", "hearing_place": "г. Москва", "placed": "2026-11-15"}],
        contracts=[{"number": "5270", "price": 7963950.0, "security": 307200.0, "versions": [
            {"version": 1, "price": 7963950.0, "execution_end": "2026-11-30"},
            {"version": 2, "price": 7963950.0, "execution_end": "2026-12-31", "change_reason": "Дополнительное соглашение", "change_date": "2026-07-03"},
        ]}],
        customer_history=[{"number": "32615684320", "title": "Поставка счётчиков", "okpd2": "26.51.63.130", "okpd2_matched": True,
                           "price": 2650433.33, "final_price": 2502150.0, "published": "2026-02-09"}],
    ))
    db_session.flush()
    sections = bitrix_ai_service.build_sections(db_session, tender)
    complaint = next(r for r in sections["fas"] if r["key"] == "complaint-2026001")
    assert complaint["type"] == "fas_decision" and complaint["data"]["hearing_place"] == "г. Москва"
    assert complaint["data"]["prescription"] == "Не выдано"
    edition = next(r for r in sections["contract"] if r["key"] == "registry-contract-5270-v2")
    assert "Окончание исполнения: 2026-11-30 → 2026-12-31" in edition["data"]["changes"]
    history = next(r for r in sections["preparation"] if r["key"].startswith("customer-history"))
    assert history["data"]["final_price"] == 2502150.0 and history["data"]["publish_date"] == "09.02.2026"


def test_trade_date_row_without_timezone(db_session):
    from app.models.tender_document import TenderDocument

    tender = _tender(db_session)
    db_session.add(TenderDocument(tender_id=tender.id, file_name="Извещение.docx", source_url="https://e.test/n",
                                  extracted_text="Дата проведения переторжки: 02.12.2026 г. в 13:00 (время местное).",
                                  document_class="notice"))
    db_session.flush()
    sections = {name: [] for name in bitrix_ai_service.SECTIONS}
    bitrix_extra_rows.trade_date_rows(db_session, tender, sections)
    (row,) = sections["preparation"]
    assert row["data"]["document_date"] == "02.12.2026" and row["data"]["document_time"] == "13:00"
    assert row["data"]["timezone"].startswith("в документе не указан")
    assert "rebid_date" not in bitrix_ai_service.build_deal_fields(db_session, tender)


# --- п. 65: письма площадок ------------------------------------------------------------------------


def _mail(subject: str, body: str, message_id: str = "<m1@etp>"):
    from email.mime.text import MIMEText

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = "noreply@etp.example"
    message["Message-ID"] = message_id
    message["Date"] = "Wed, 18 Nov 2026 10:00:00 +0300"
    return message


def test_mail_application_request_becomes_event(db_session):
    from app.models.tender_event import TenderEvent
    from app.services import mail_inbox_service

    tender = _tender(db_session)
    assert not mail_inbox_service.classify("Разъяснения положений документации по закупке 32615974226", "Ответ на запрос")
    record = mail_inbox_service.handle_message(
        db_session,
        _mail("Запрос заказчика о разъяснении заявки", "По процедуре 32615974226 поступил запрос заказчика о разъяснении "
              "положений вашей заявки. Срок ответа — 20.11.2026."),
    )
    assert record is not None and record.tender_id == tender.id
    event = db_session.query(TenderEvent).filter_by(tender_id=tender.id).one()
    assert event.event_type == "DOCUMENT_ADDED" and event.data["application_request"] is True
    # Повтор того же письма — не новое событие.
    assert mail_inbox_service.handle_message(db_session, _mail("Запрос заказчика о разъяснении заявки", "32615974226 запрос о разъяснении заявки")) is None


# --- п. 32: банковские гарантии -----------------------------------------------------------------


def test_guarantee_coverage_on_deadline_shift(db_session):
    from app.services import guarantee_service, tender_monitor_service

    tender = _tender(db_session)
    guarantee_service.create(db_session, tender, {"kind": "APPLICATION", "number": "БГ-1", "valid_until": date(2026, 11, 25)}, actor=None)
    change = {"event_type": "APPLICATION_END_CHANGED", "field_key": "application_end", "title": "Срок",
              "old_value": "2026-11-20T10:00+03:00", "new_value": "2026-11-27T10:00+03:00"}
    (enriched,) = tender_monitor_service.enrich_document_changes(db_session, tender, [change])
    assert enriched["data"]["guarantee_extension_required"] is True
    assert "требуется продление" in enriched["note"]


def test_guarantee_api(client, admin_token):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        tender = _tender(db)
        db.commit()
        tender_id = str(tender.id)
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.post(f"/tenders/{tender_id}/guarantees", headers=headers,
                           json={"kind": "CONTRACT", "number": "7", "valid_until": "2027-01-31"})
    assert response.status_code == 201, response.text
    (item,) = response.json()
    assert item["kind_label"] == "Обеспечение исполнения договора" and item["covered"] is None
    assert client.delete(f"/tenders/{tender_id}/guarantees/{item['id']}", headers=headers).json() == []


# --- пп. 11–12: ресурсы компании ----------------------------------------------------------------


def test_qualification_uses_company_resources():
    from app.services import qualification_service as q

    facts = {"licenses": [], "projects": 0, "years": 0, "headcount": 283, "revenue": (2.9e9, "2,9 млрд руб.", 2025),
             "staff": [{"role": "Электромонтёр", "count": 4, "qualification": "III группа по электробезопасности"}],
             "facilities": [{"kind": "Склад", "address": "Ростов"}], "equipment": []}
    staff = q.assess("Электротехнический персонал не менее 10 человек с группой по электробезопасности", facts)
    assert staff.status == "missing" and "не менее 10" in staff.missing[0]
    assert q.assess("Наличие склада", facts).status == "meets"
    assert q.assess("Наличие поверочного оборудования", facts).status == "check"
    finance = q.assess("Выручка за последний отчётный год не менее 100 млн руб.", facts)
    assert finance.status == "meets" and "Rusprofile" in finance.basis


# --- п. 29: цитата к риску ----------------------------------------------------------------------


def test_risk_gets_quote_from_contract_conditions(db_session):
    from app.models.tender_contract_conditions import TenderContractConditions

    tender = _tender(db_session)
    db_session.add(TenderContractConditions(tender_id=tender.id, payload={"conditions": [
        {"kind": "PENALTY", "value": "Пеня 0,1 % за каждый день просрочки поставки", "document": "Договор.docx",
         "quote": "За каждый день просрочки поставки Поставщик уплачивает пеню в размере 0,1 % от цены договора"},
    ]}))
    db_session.flush()
    found = bitrix_extra_rows.risk_evidence(db_session, tender, "Высокая пеня за просрочку поставки от цены договора", [], {})
    assert found[0] == "Договор.docx" and "пеню" in found[1]
    assert bitrix_extra_rows.risk_evidence(db_session, tender, "Риск неоплаты", [], {}) is None
