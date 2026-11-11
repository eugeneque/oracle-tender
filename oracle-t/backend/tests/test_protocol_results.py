"""Итоги процедуры из протоколов (11.11.2026, раздел 96 ARCHITECTURE.md).

Модель не вызывается: ответ подставляется, проверяется то, что делает код вокруг неё —
сведение участников по протоколам, победитель только из итогового протокола, противоречия,
наш результат только по нашему ИНН (без LOSS при неучастии), строки Bitrix24, событие
RESULT_CHANGED и разбор только при новом протоколе.
"""

from __future__ import annotations

import uuid

from app.models.company_profile import CompanyProfile
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services import bitrix_ai_service, protocol_results_service as service
from app.services import tender_event_service, tender_monitor_service
from app.services.protocol_results_service import ParticipantAnswer, ProtocolAnswer, ProtocolsAnswer

OURS = "6154125635"
RIVAL = "7701234567"

FIRST = "Протокол рассмотрения первых частей.pdf"
FINAL = "Итоговый протокол.pdf"
REV2 = "Итоговый протокол (ред. 2).pdf"


def _tender(db, *, protocols=(FIRST, FINAL)) -> Tender:
    source = Source(key=f"pr_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3263{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков",
        currency="RUB",
        price=1_000_000,
        source_url="https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=1",
    )
    db.add(tender)
    db.flush()
    for name in protocols:
        _protocol(db, tender, name)
    return tender


def _protocol(db, tender: Tender, name: str) -> TenderDocument:
    doc = TenderDocument(
        tender_id=tender.id,
        file_name=name,
        source_url=f"https://example.test/{uuid.uuid4().hex}.pdf",
        extracted_text=f"{name}\nУчастник ООО «Ромашка», ИНН {RIVAL}, цена 900 000,00 руб.",
        document_class="protocol",
    )
    db.add(doc)
    db.flush()
    return doc


def _ours(db) -> None:
    if not db.query(CompanyProfile).filter_by(inn=OURS).first():
        db.add(CompanyProfile(legal_name="ООО «МИРТЕК»", inn=OURS, is_primary=True))
        db.flush()


def _prot(document=FINAL, **kw) -> ProtocolAnswer:
    final = document == FINAL
    base = dict(
        document=document, kind="RESULTS" if final else "FIRST_PARTS", is_final="yes" if final else "no",
        protocol_date="21.12.2026", protocol_number="", lot="1",
        procedure_outcome="HELD" if final else "IN_PROGRESS", failed_reason="", quote="Признать победителем",
    )
    return ProtocolAnswer(**{**base, **kw})


def _part(document=FINAL, **kw) -> ParticipantAnswer:
    base = dict(
        document=document, lot="1", name="ООО «Ромашка»", inn=RIVAL, kpp="770101001", application_number="1",
        price="900 000,00", currency="RUB", vat="с НДС", admission="ADMITTED", rejection_reason="",
        rejection_category="", place="1", is_winner="yes" if document == FINAL else "unknown", delivery_term="",
        quote="ООО «Ромашка» … 900 000,00",
    )
    return ParticipantAnswer(**{**base, **kw})


def _our_part(document=FINAL, **kw) -> ParticipantAnswer:
    base = dict(name="ООО «МИРТЕК»", inn=OURS, kpp="", application_number="2", price="950000", place="2",
                is_winner="no", quote="ООО «МИРТЕК» … 950 000")
    return _part(document, **{**base, **kw})


def _answer(protocols=None, participants=None) -> ProtocolsAnswer:
    return ProtocolsAnswer(
        protocols=protocols if protocols is not None else [_prot(FIRST), _prot()],
        participants=participants if participants is not None else [_part(FIRST), _part(), _our_part(FIRST), _our_part()],
    )


def test_participants_merged_across_protocols_final_wins():
    data = service.normalize(
        _answer(participants=[_part(FIRST, price="", admission="ADMITTED"), _part(price="880 000")]),
        ours={OURS},
    )
    (rival,) = data["participants"]
    assert rival["price"] == 880000 and rival["in_final"] is True
    assert [s["document"] for s in rival["sources"]] == [FIRST, FINAL]
    (lot,) = data["lots"]
    assert lot["winner"] == "ООО «Ромашка»" and lot["winner_inn"] == RIVAL and lot["winner_price"] == 880000
    assert lot["final"] is True and lot["outcome"] == "HELD" and lot["protocol_document"] == FINAL


def test_inn_format_checked():
    data = service.normalize(_answer(participants=[_part(inn="12345", kpp="1")]))
    assert "inn" not in data["participants"][0] and "kpp" not in data["participants"][0]


def test_winner_only_from_final_protocol():
    data = service.normalize(
        _answer(protocols=[_prot(FIRST)], participants=[_part(FIRST, is_winner="yes"), _our_part(FIRST)]),
        ours={OURS},
    )
    (lot,) = data["lots"]
    assert "winner" not in lot and lot["final"] is False
    assert lot["preliminary_winner"] == "ООО «Ромашка»"
    assert lot["our_result"] == "PENDING"  # предварительный итог — не проигрыш


def test_our_result_codes():
    ours = {OURS}
    loss = service.normalize(_answer(), ours=ours)["lots"][0]
    assert loss["our_result"] == "LOSS"
    win = service.normalize(
        _answer(participants=[_part(is_winner="no"), _our_part(is_winner="yes")]), ours=ours
    )["lots"][0]
    assert win["our_result"] == "WIN" and win["winner_inn"] == OURS
    rejected = service.normalize(
        _answer(participants=[_part(), _our_part(admission="REJECTED", rejection_reason="Не соответствует п. 3.1 ТЗ")]),
        ours=ours,
    )["lots"][0]
    assert rejected["our_result"] == "NOT_ADMITTED" and rejected["our_rejection_reason"] == "Не соответствует п. 3.1 ТЗ"


def test_no_loss_when_we_did_not_participate():
    lot = service.normalize(_answer(participants=[_part()]), ours={OURS})["lots"][0]
    assert "our_result" not in lot  # нас нет в протоколе — результата нет, а не LOSS


def test_conflicts_block_result():
    data = service.normalize(
        _answer(
            protocols=[_prot(), _prot(REV2, is_final="yes")],
            participants=[_part(), _our_part(), _our_part(REV2, is_winner="yes")],
        ),
        ours={OURS},
    )
    (lot,) = data["lots"]
    assert "winner" not in lot and any("Несколько победителей" in c for c in lot["conflicts"])
    assert lot["our_result"] == "PENDING"

    prices = service.normalize(
        _answer(protocols=[_prot(), _prot(REV2, is_final="yes")], participants=[_part(), _part(REV2, price="870000")])
    )
    assert any("Разные цены" in c for c in prices["lots"][0]["conflicts"])
    assert prices["participants"][0]["conflict"] is True


def test_lots_kept_apart():
    data = service.normalize(
        _answer(
            protocols=[_prot(), _prot(lot="2")],
            participants=[_part(), _part(lot="2", is_winner="no"), _our_part(lot="2", is_winner="yes")],
        ),
        ours={OURS},
    )
    lots = {lot["lot_number"]: lot for lot in data["lots"]}
    assert lots["1"]["winner_inn"] == RIVAL and "our_result" not in lots["1"]
    assert lots["2"]["winner_inn"] == OURS and lots["2"]["our_result"] == "WIN"


def test_bitrix_rows_and_result_status(db_session, client, monkeypatch):
    _ours(db_session)
    tender = _tender(db_session)
    assert bitrix_ai_service.build_deal_fields(db_session, tender)["result_status"] == "PENDING"
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _answer())
    assert service.extract(db_session, tender) == {"protocols": 2, "participants": 2}

    sections = bitrix_ai_service.build_sections(db_session, tender)
    rows = sections["participants_prices"]
    (result,) = [r for r in rows if r["type"] == "procedure_result"]
    assert result["key"] == "procedure-result" and result["status"] == "extracted"
    assert result["data"]["winner_inn"] == RIVAL and result["data"]["final_price"] == 900000
    assert result["data"]["deviation_percent"] == -10.0
    assert result["data"]["our_result"] == "Проигрыш"
    assert result["data"]["protocol_kind"] == "Протокол подведения итогов"
    participants = {r["data"].get("inn"): r for r in rows if r["type"] == "participant"}
    assert participants[RIVAL]["data"]["is_winner"] is True and participants[RIVAL]["data"]["admission"] == "Допущена"
    assert participants[OURS]["data"]["is_ours"] is True and "is_winner" not in participants[OURS]["data"]
    assert len(participants[RIVAL]["evidence"]) == 2

    assert bitrix_ai_service.build_deal_fields(db_session, tender)["result_status"] == "LOSS"
    again = bitrix_ai_service.build_sections(db_session, tender)
    assert [r["key"] for r in again["participants_prices"]] == [r["key"] for r in rows]


def test_rival_winner_without_us_keeps_pending(db_session, client, monkeypatch):
    _ours(db_session)
    tender = _tender(db_session)
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _answer(participants=[_part()]))
    service.extract(db_session, tender)
    assert bitrix_ai_service.build_deal_fields(db_session, tender)["result_status"] == "PENDING"


def test_refresh_if_new_only_on_new_protocol(db_session, client, monkeypatch):
    tender = _tender(db_session)
    calls = []
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: calls.append(1) or _answer())
    assert service.refresh_if_new(db_session, tender) is True
    assert service.refresh_if_new(db_session, tender) is False  # те же протоколы — без модели
    _protocol(db_session, tender, "Протокол переторжки.pdf")
    assert service.refresh_if_new(db_session, tender) is True
    assert len(calls) == 2


def test_monitoring_result_changed_event(db_session, client, monkeypatch):
    _ours(db_session)
    tender = _tender(db_session, protocols=(FIRST,))
    from app.models.tender_event import TenderMonitor

    db_session.add(TenderMonitor(tender_id=tender.id, enabled=True))
    db_session.flush()

    def refresh(db, t):  # площадку и ЕИС не трогаем — только шаг «протоколы»
        tender_monitor_service._refresh_protocols(db, t)
        return []

    monkeypatch.setattr(tender_monitor_service, "refresh_tender", refresh)

    monkeypatch.setattr(
        service, "run_structured",
        lambda *a, **k: _answer(protocols=[_prot(FIRST)], participants=[_part(FIRST), _our_part(FIRST)]),
    )
    assert tender_monitor_service.check_tender(db_session, tender) == []  # первая проверка — слепок

    _protocol(db_session, tender, FINAL)
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _answer(participants=[_part(is_winner="no"), _our_part(is_winner="yes")]))
    events = tender_monitor_service.check_tender(db_session, tender)
    changed = [e for e in events if e.event_type == "RESULT_CHANGED"]
    assert len(changed) == 1
    assert changed[0].old_value == "PENDING" and changed[0].new_value == "WIN"

    payload = tender_event_service.build_events_payload(db_session, tender, changed)
    assert payload["deal_fields"] == {"result_status": "WIN"}
    assert any(r["type"] == "procedure_result" for r in payload["sections"]["participants_prices"])

    # Та же проверка ещё раз — новых событий о результате нет.
    assert [e for e in tender_monitor_service.check_tender(db_session, tender) if e.event_type == "RESULT_CHANGED"] == []


def test_protocol_results_api(client, admin_token):
    from app.db.session import SessionLocal

    headers = {"Authorization": f"Bearer {admin_token}"}
    with SessionLocal() as db:
        tender = _tender(db, protocols=())
        db.commit()
        tender_id = str(tender.id)
    response = client.get(f"/tenders/{tender_id}/protocol-results", headers=headers)
    assert response.status_code == 200 and response.json()["lots"] == []
    response = client.post(f"/tenders/{tender_id}/protocol-results/extract", headers=headers)
    assert response.status_code == 409 and "нет протоколов" in response.json()["detail"]
