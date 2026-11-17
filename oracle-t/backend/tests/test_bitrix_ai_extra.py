"""Дополнение п.95 «Что еще нужно от ИИ» (17.11.2026, раздел 113 ARCHITECTURE.md).

Без сети и без модели: карточка ЕИС и итоги протоколов подставляются готовыми, проверяется код —
разбор таблиц карточки, события мониторинга по этапу/журналу/редакциям, сравнение текстов,
квалификация, редакции и раунды протоколов, выводы по итогам и строки Bitrix24.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.models.analysis import Requirement
from app.models.company_profile import CompanyProfile
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_contract_conditions import TenderContractConditions
from app.models.tender_document import TenderDocument
from app.models.tender_protocol_results import TenderProtocolResults
from app.services import (
    bitrix_ai_service,
    bitrix_extra_rows,
    document_diff,
    eis_card_tables,
    qualification_service,
    tender_monitor_service as monitor,
)
from app.services.protocol_results_service import (
    ParticipantAnswer,
    ProtocolAnswer,
    ProtocolsAnswer,
    RoundAnswer,
    normalize,
)
from app.services.tender_monitor_service import diff_snapshots

OURS = "6154125635"
RIVAL = "7701234567"


def _card(**tables) -> dict:
    return {
        "sections": [{"title": "Общая информация", "fields": [["Этап закупки", tables.pop("stage", "Подача заявок")]]}],
        "tables": {
            key: {"headers": ["", "Наименование документа", "Редакция", "Статус", "Размещено"] if key == "protocols"
                  else ["Наименование документа", "Редакция", "Статус", "Размещено"], "rows": rows}
            for key, rows in tables.items() if key != "events" and key != "lots"
        } | ({"events": {"headers": ["Дата и время", "Событие"], "rows": tables["events"]}} if "events" in tables else {})
          | ({"lots": {"headers": ["Номер, наименование лота"], "rows": tables["lots"]}} if "lots" in tables else {}),
    }


# --- карточка ЕИС -------------------------------------------------------------------------------


def test_card_tables_parsed_with_revision_and_zone():
    payload = _card(
        protocols=[["", "Итоговый протокол №1-01", "2", "Действующая", "15.05.2026 09:07(МСК+7)"],
                   ["", "Итоговый протокол №1-01", "1", "Недействующая", "14.05.2026 09:07(МСК+7)"]],
        lots=[["1 Поставка счётчиков", "Нет", "Начальная (максимальная) цена договора: 474 661,80 ₽", "26.51.63.130 Счетчики"],
              ["№", "Критерий", "Описание"]],
        stage="Определение поставщика приостановлено",
    )
    protocols = eis_card_tables.documents_map(payload, "protocols")
    assert protocols["Итоговый протокол №1-01"]["revision"] == "2"
    assert protocols["Итоговый протокол №1-01"]["placed"].endswith("+10:00")  # МСК+7 = UTC+10
    (lot,) = eis_card_tables.lots(payload)
    assert lot["lot_number"] == "1" and lot["price"] == 474661.8 and lot["okpd2"].startswith("26.51.63.130")
    assert eis_card_tables.is_suspended(eis_card_tables.official_stage(payload))


# --- события мониторинга ------------------------------------------------------------------------


def _snap(**extra):
    base = {
        "fields": {"application_end": "2026-11-20T10:00+03:00", "price": "1000000.00", "status": "collecting_bids"},
        "security": {}, "documents": {}, "stage": "Подача заявок", "journal": {}, "card_protocols": {},
        "card_contracts": {}, "card_changes": {}, "positions": {},
    }
    base.update(extra)
    return base


def _types(changes):
    return sorted(c["event_type"] for c in changes)


def test_old_snapshot_without_card_blocks_gives_no_events():
    old = {key: value for key, value in _snap().items() if key in ("fields", "security", "documents")}
    new = _snap(stage="Работа комиссии", journal={"k": {"text": "Закупка приостановлена", "at": None}},
                card_contracts={"Договор №1": {"revision": "1"}})
    assert diff_snapshots(old, new) == []


def test_suspension_and_resumption_by_official_stage():
    suspended = diff_snapshots(_snap(fields={"status": "evaluation"}),
                               _snap(fields={"status": "collecting_bids"}, stage="Определение поставщика приостановлено"))
    assert _types(suspended) == ["PROCEDURE_SUSPENDED"]  # статус СОВЫ о том же — не дублируется
    resumed = diff_snapshots(_snap(stage="Определение поставщика приостановлено"), _snap(stage="Работа комиссии"))
    assert _types(resumed) == ["PROCEDURE_RESUMED"]
    assert resumed[0]["new_value"] == "Работа комиссии"


def test_journal_fas_and_round_cancellation():
    new = _snap(
        fields={"application_end": "2026-11-20T10:00+03:00", "price": "1000000.00", "status": "collecting_bids",
                "rebid_date": "2026-11-25T10:00+03:00"},
        journal={
            "a": {"text": "Поступила жалоба на действия заказчика", "at": "2026-11-10T10:00+03:00"},
            "b": {"text": "Отменена переторжка (подача дополнительных ценовых предложений)", "at": "2026-11-11T10:00+03:00"},
        },
    )
    old = _snap(fields={**new["fields"]})
    changes = diff_snapshots(old, new)
    fas = next(c for c in changes if c["data"].get("fas_event"))
    assert fas["event_type"] == "PROCEDURE_STATUS_CHANGED" and fas["effective_at"] == "2026-11-10T10:00+03:00"
    cancel = next(c for c in changes if c["event_type"] == "REBID_DATE_CHANGED")
    assert cancel["old_value"] == "2026-11-25T10:00+03:00" and cancel["new_value"] is None
    assert cancel["data"] == {"round_cancelled": True}


def test_protocol_revision_contract_and_customer_request():
    old = _snap(card_protocols={"Итоговый протокол": {"revision": "1", "status": "Действующая"}},
                card_contracts={"Договор №5": {"revision": "1"}})
    new = _snap(
        card_protocols={"Итоговый протокол": {"revision": "2", "status": "Действующая", "placed": "2026-11-12T09:00+03:00"}},
        card_contracts={"Договор №5": {"revision": "2"}, "Договор №6": {"revision": "1"}},
        card_changes={"Запрос заказчика о разъяснении положений заявки": {"revision": "1"}},
    )
    changes = diff_snapshots(old, new)
    revision = next(c for c in changes if c["event_type"] == "PROTOCOL_PUBLISHED")
    assert revision["source_version"] == "2" and revision["data"]["is_revision"] is True
    assert sum(c["event_type"] == "CONTRACT_CHANGED" for c in changes) == 2
    request = next(c for c in changes if c["event_type"] == "DOCUMENT_ADDED")
    assert request["data"] == {"customer_request": True}


def test_positions_quantity_change():
    changes = diff_snapshots(
        _snap(positions={"1. Счётчик однофазный": "100", "2. Пломба": "50"}),
        _snap(positions={"1. Счётчик однофазный": "120", "3. Модем": "5"}),
    )
    (change,) = changes
    assert change["event_type"] == "TECHNICAL_REQUIREMENT_CHANGED"
    fields = {c["field"]: (c["old_value"], c["new_value"]) for c in change["data"]["changes"]}
    assert fields["1. Счётчик однофазный"] == ("100", "120")
    assert fields["2. Пломба"] == ("50", None) and fields["3. Модем"] == (None, "5")


# --- тексты документов --------------------------------------------------------------------------


def test_diff_texts_and_clarification():
    old = "1. Общие положения\n5.2. Срок поставки — 30 календарных дней\n7. Гарантия 36 месяцев"
    new = "1. Общие положения\n5.2. Срок поставки — 45 календарных дней\n7. Гарантия 36 месяцев"
    diff = document_diff.diff_texts(old, new)
    assert diff["total"] == 1 and not diff["noisy"]
    (change,) = diff["changes"]
    assert change["field"] == "Пункт 5.2." and "30" in change["old_value"] and "45" in change["new_value"]
    excerpt = document_diff.clarification_excerpt("Запрос: можно ли эквивалент?\nОтвет: Эквивалент допускается, срок поставки не меняется, цена договора прежняя.")
    assert excerpt.startswith("Эквивалент допускается")
    assert document_diff.impact_areas(excerpt) == ["ТЗ и характеристики", "Цена", "Сроки"] or "Сроки" in document_diff.impact_areas(excerpt)


def _tender(db, **kw) -> Tender:
    source = Source(key=f"bx_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=kw.pop("external_id", "32616000001"),
        registry_number=kw.pop("registry_number", "32616000001"),
        title="Поставка счётчиков",
        currency="RUB",
        price=1_000_000,
        **kw,
    )
    db.add(tender)
    db.flush()
    return tender


def test_new_contract_edition_compared_by_text(db_session):
    tender = _tender(db_session)
    for name, text in (("Проект договора.docx", "5.2. Срок поставки 30 дней\n8. Оплата 30 дней"),
                       ("Проект договора (изм.).docx", "5.2. Срок поставки 45 дней\n8. Оплата 30 дней")):
        db_session.add(TenderDocument(tender_id=tender.id, file_name=name, source_url=f"https://e.test/{name}",
                                      extracted_text=text, document_class="contract"))
        db_session.flush()
    change = {"event_type": "CONTRACT_CHANGED", "field_key": "doc:https://e.test/Проект договора (изм.).docx",
              "title": "Новая редакция проекта договора", "old_value": None, "new_value": "Проект договора (изм.).docx"}
    (enriched,) = monitor.enrich_document_changes(db_session, tender, [change])
    assert enriched["data"]["compared_with"] == "Проект договора.docx"
    assert "30" in enriched["old_value"] and "45" in enriched["new_value"]


# --- квалификация -------------------------------------------------------------------------------


def test_qualification_parse_and_company_match():
    facts = {"licenses": ["ФСБ", "ISO 9001"], "projects": 5, "years": 13}
    q = qualification_service.assess(
        "Опыт исполнения аналогичных договоров не менее 3 лет за последние 3 года, подтверждается копиями договоров и актов",
        facts,
    )
    assert q.category == "EXPERIENCE" and q.status == "meets"
    assert q.threshold == "не менее 3 лет" and q.period.startswith("за последние 3 года")
    assert q.confirmation.startswith("подтверждается копиями договоров")
    law = qualification_service.assess("Отсутствие у участника недоимки по налогам, сборам за последний отчётный период", facts)
    assert law.status == "declaration"
    lic = qualification_service.assess("Наличие лицензии ФСБ на работу с шифровальными средствами", facts)
    assert lic.status == "meets"
    nmc = qualification_service.assess("Сумма исполненных договоров не менее начальной (максимальной) цены договора", facts)
    assert "начальной" in nmc.threshold


# --- протоколы: редакции, раунды, уверенность ---------------------------------------------------


def _prot(document, **kw):
    base = dict(document=document, kind="RESULTS", is_final="yes", protocol_date="21.12.2026", protocol_number="",
                lot="1", procedure_outcome="HELD", failed_reason="", amends="no", amended_document="", quote="Победитель")
    return ProtocolAnswer(**{**base, **kw})


def _part(document, **kw):
    base = dict(document=document, lot="1", name="ООО «Ромашка»", inn=RIVAL, kpp="", application_number="1",
                price="900000", currency="RUB", vat="", admission="ADMITTED", rejection_reason="", rejection_category="",
                place="1", is_winner="yes", delivery_term="", quote="Ромашка 900000")
    return ParticipantAnswer(**{**base, **kw})


def test_protocol_revision_supersedes_and_rounds():
    answer = ProtocolsAnswer(
        protocols=[_prot("Итоговый.pdf"), _prot("Изменение итогового.pdf", amends="yes", amended_document="Итоговый")],
        participants=[_part("Итоговый.pdf"), _part("Изменение итогового.pdf", name="ООО «Лютик»", inn="7709876543")],
        rounds=[RoundAnswer(document="Итоговый.pdf", lot="1", round="1", name="ООО «Ромашка»", inn=RIVAL,
                            application_number="", price="950000", bid_time="10:01", quote=""),
                RoundAnswer(document="Изменение итогового.pdf", lot="1", round="2", name="ООО «Лютик»", inn="7709876543",
                            application_number="", price="900000", bid_time="10:05", quote=""),
                RoundAnswer(document="Изменение итогового.pdf", lot="1", round="2", name="Без цены", inn="",
                            application_number="", price="", bid_time="", quote="")],
    )
    result = normalize(answer, ours={OURS})
    (lot,) = result["lots"]
    assert lot["winner_inn"] == "7709876543"  # заменённый протокол больше не источник итога
    assert lot["superseded_documents"] == ["Итоговый.pdf"] and lot["revision_of"] == "Итоговый"
    assert [r["price"] for r in result["rounds"]] == [900000]  # раунд из заменённого и без цены — отброшены
    assert lot["confidence"]["winner"] == 0.9 and lot["confidence"]["version"] == 0.7


# --- выводы по итогам и строки Bitrix24 ---------------------------------------------------------


def _results(db, tender, *, our, winner_price=900000, our_extra=None):
    ours = {"lot_number": "1", "name": "ООО «МИРТЕК»", "inn": OURS, "is_ours": True, "price": 850000, **(our_extra or {})}
    rival = {"lot_number": "1", "name": "ООО «Ромашка»", "inn": RIVAL, "price": winner_price, "delivery_term": "10 дней"}
    lot = {"lot_number": "1", "final": True, "protocol_document": "Итоговый.pdf", "protocol_date": "10.11.2026",
           "winner": "ООО «МИРТЕК»" if our == "WIN" else "ООО «Ромашка»", "winner_inn": OURS if our == "WIN" else RIVAL,
           "winner_price": winner_price, "our_result": our, "quote": "Итоги", "conflicts": []}
    db.add(TenderProtocolResults(tender_id=tender.id, payload={"protocols": [], "participants": [ours, rival],
                                                                 "lots": [lot], "rounds": [], "documents": []}))
    db.add(TenderCard(tender_id=tender.id, payload=_card(
        protocols=[["", "Итоговый протокол №1-01", "1", "Действующая", "10.11.2026 09:00(МСК)"]])))
    db.flush()


def test_win_confirmation_and_signing_deadline(db_session):
    tender = _tender(db_session)
    _results(db_session, tender, our="WIN")
    db_session.add(TenderContractConditions(tender_id=tender.id, payload={"conditions": [
        {"kind": "SIGNING_DEADLINE", "presence": "PRESENT", "value": "Договор подписывается не ранее 10 и не позднее 20 дней",
         "term": "не ранее 10 и не позднее 20 дней", "day_type": "CALENDAR",
         "trigger_event": "с даты размещения итогового протокола", "lot_number": "1", "quote": "не позднее 20 дней"},
    ]}))
    db_session.flush()
    sections = {name: [] for name in bitrix_ai_service.SECTIONS}
    bitrix_extra_rows.outcome_rows(db_session, tender, [], {}, sections)
    win = next(r for r in sections["participants_prices"] if r["key"] == "win-confirmation")
    assert win["data"]["confirmed"] == "Да" and win["status"] == "extracted"
    signing = next(r for r in sections["contract"] if r["key"] == "signing-deadline")
    assert signing["data"]["deadline"] == "30.11.2026"  # 10.11 + 20 календарных дней
    restrictions = next(r for r in sections["contract"] if r["key"] == "signing-restrictions")
    assert restrictions["data"]["result"].startswith("По карточке и журналу ЕИС ограничений не найдено")


def test_rejection_gives_appeal_grounds_and_deadline(db_session):
    tender = _tender(db_session)
    _results(db_session, tender, our="NOT_ADMITTED", our_extra={
        "admission": "REJECTED", "rejection_category": "TECHNICAL",
        "rejection_reason": "Предложенный счётчик не поддерживает протокол СПОДЭС",
    })
    requirement = Requirement(tender_id=tender.id, text="Поддержка протокола СПОДЭС счётчиком", kind="product",
                              criticality="critical")
    db_session.add(requirement)
    db_session.flush()
    sections = {name: [] for name in bitrix_ai_service.SECTIONS}
    bitrix_extra_rows.outcome_rows(db_session, tender, [requirement], {}, sections)
    appeal = next(r for r in sections["fas"] if r["key"] == "fas-appeal-result")
    assert "нет ссылки на пункт документации" in appeal["data"]["grounds"]
    assert appeal["data"]["deadline"] == "20.11.2026"  # 223-ФЗ: 10 дней от размещения протокола
    technical = next(r for r in sections["participants_prices"] if r["key"] == "technical-rejection")
    assert "Поддержка протокола СПОДЭС" in technical["data"]["requirements"]


def test_missing_from_protocol_when_application_submitted(db_session):
    tender = _tender(db_session, stage="application_submitted")
    db_session.add(TenderProtocolResults(tender_id=tender.id, payload={
        "participants": [{"lot_number": "1", "name": "ООО «Ромашка»", "inn": RIVAL}],
        "lots": [{"lot_number": "1", "final": True, "winner": "ООО «Ромашка»", "winner_inn": RIVAL}],
    }))
    db_session.flush()
    sections = {name: [] for name in bitrix_ai_service.SECTIONS}
    bitrix_extra_rows.outcome_rows(db_session, tender, [], {}, sections)
    assert any(r["key"] == "our-application-missing" for r in sections["participants_prices"])


def test_clarification_and_qualification_rows_in_payload(db_session):
    tender = _tender(db_session)
    if not db_session.query(CompanyProfile).filter_by(inn=OURS).first():
        db_session.add(CompanyProfile(legal_name="ООО «МИРТЕК»", inn=OURS, years_of_experience=13))
    db_session.add_all([
        Requirement(tender_id=tender.id, text="Цвет корпуса — по согласованию с заказчиком", kind="product", criticality="minor"),
        Requirement(tender_id=tender.id, text="Опыт поставки не менее 5 лет", kind="participant", criticality="critical"),
    ])
    db_session.flush()
    sections = bitrix_ai_service.build_sections(db_session, tender)
    clarification = next(r for r in sections["preparation"] if r["type"] == "clarification")
    assert clarification["data"]["question"].startswith("Какие условия согласования")
    assert "Просим разъяснить" in clarification["data"]["request_draft"]
    assert "ТЗ и характеристики" in clarification["data"]["affects"]
    qualification = next(r for r in sections["preparation"] if r["type"] == "qualification")
    assert qualification["data"]["threshold"] == "не менее 5 лет"
    assert qualification["data"]["qualification_kind"] == "Опыт исполнения договоров"


def test_term_days_and_law():
    assert bitrix_extra_rows.term_days("не ранее 10 и не позднее 20 дней") == 20
    assert bitrix_extra_rows.term_days("в течение 30 (тридцати) календарных дней") == 30
    assert bitrix_extra_rows._add_days(date(2026, 11, 13), 2, True) == date(2026, 11, 17)  # пятница + 2 рабочих
    assert bitrix_extra_rows.law_of(Tender(registry_number="0123456789012345678901"[:19])) == "44"


def test_bank_guarantee_check_when_deadline_moves(db_session):
    tender = _tender(db_session)
    db_session.add(TenderContractConditions(tender_id=tender.id, payload={"conditions": [
        {"kind": "APPLICATION_SECURITY", "presence": "PRESENT", "value": "1 % НМЦК", "percent": 1,
         "allowed_forms": "денежные средства, банковская гарантия", "lot_number": "1"},
    ]}))
    db_session.flush()
    change = {"event_type": "APPLICATION_END_CHANGED", "field_key": "application_end", "title": "Изменился срок",
              "old_value": "2026-11-20T10:00+03:00", "new_value": "2026-11-27T10:00+03:00"}
    (enriched,) = monitor.enrich_document_changes(db_session, tender, [change])
    assert enriched["data"] == {"bank_guarantee_check": "APPLICATION"}
    assert "на 7 дн. позже" in enriched["note"]


def test_security_from_documentation_changes():
    changes = diff_snapshots(
        _snap(doc_security={"APPLICATION_SECURITY:1": "1 %"}),
        _snap(doc_security={"APPLICATION_SECURITY:1": "5 %"}),
    )
    (change,) = changes
    assert change["event_type"] == "SECURITY_CHANGED" and change["data"] == {"security_kind": "APPLICATION"}


def test_positions_change_scoped_to_lot():
    (change,) = diff_snapshots(
        _snap(positions={"1. Счётчик": "10", "1. Счётчик (лот 2)": "5"}),
        _snap(positions={"1. Счётчик": "10", "1. Счётчик (лот 2)": "7"}),
    )
    assert change["scope"] == "LOT" and change["lot_number"] == "2"


def test_procedure_result_row_carries_confidence_and_revision(db_session):
    tender = _tender(db_session)
    db_session.add(TenderProtocolResults(tender_id=tender.id, payload={"participants": [], "lots": [{
        "lot_number": "1", "final": True, "winner": "ООО «Ромашка»", "winner_inn": RIVAL, "quote": "Итоги",
        "revision_of": "Итоговый", "superseded_documents": ["Итоговый.pdf"],
        "confidence": {"winner": 0.9, "lot": 0.9, "version": 0.7, "basis": "есть изменённые редакции протокола"},
    }]}))
    db_session.flush()
    sections = {name: [] for name in bitrix_ai_service.SECTIONS}
    bitrix_ai_service.protocol_rows(db_session, tender, sections)
    (row,) = sections["participants_prices"]
    assert row["confidence"] == 0.7 and row["data"]["confidence_version"] == 0.7
    assert row["data"]["superseded_documents"] == "Итоговый.pdf"
