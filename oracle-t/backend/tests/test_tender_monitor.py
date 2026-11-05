"""Отслеживание изменений по закупкам и события для Bitrix24 (05.11.2026).

Сеть не используется: сравнение слепков — чистая функция, проверка закупки идёт с
`refresh=False` (площадки не опрашиваются), портал играет `httpx.MockTransport`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.models.bitrix_deal import BitrixDealLink
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.models.tender_event import EventDelivery, TenderEvent
from app.services import bitrix_ai_service, tender_event_service, tender_monitor_service as monitor
from app.services.tender_monitor_service import diff_snapshots

URL = "https://b24.example.test/local/tools/tender_ai_analysis/integration/receive.php"
SECRET = "test-secret-0123456789abcdef"


def _snap(**fields):
    base = {
        "fields": {
            "application_end": "2026-11-20T10:00+03:00",
            "auction_date": None,
            "rebid_date": None,
            "result_date": None,
            "price": "1000000.00",
            "status": "collecting_bids",
        },
        "security": {},
        "documents": {},
    }
    for key, value in fields.pop("fields", {}).items():
        base["fields"][key] = value
    base.update(fields)
    return base


def _types(changes):
    return [c["event_type"] for c in changes]


def test_no_changes_no_events():
    assert diff_snapshots(_snap(), _snap()) == []


def test_deadline_change_compares_instants_not_strings():
    same_instant = _snap(fields={"application_end": "2026-11-20T07:00+00:00"})
    assert diff_snapshots(_snap(), same_instant) == []
    moved = diff_snapshots(_snap(), _snap(fields={"application_end": "2026-11-25T10:00+03:00"}))
    assert _types(moved) == ["APPLICATION_END_CHANGED"]
    assert moved[0]["old_value"] == "2026-11-20T10:00+03:00"
    assert moved[0]["new_value"] == "2026-11-25T10:00+03:00"
    assert "effective_at" not in moved[0]  # дату торгов вместо неё не подставляем


def test_auction_first_scheduled_then_moved_use_one_code():
    first = diff_snapshots(_snap(), _snap(fields={"auction_date": "2026-11-27T11:00+03:00"}))
    assert _types(first) == ["AUCTION_DATE_CHANGED"] and first[0]["old_value"] is None
    assert first[0]["note"]  # старого значения нет — причина в note
    moved = diff_snapshots(
        _snap(fields={"auction_date": "2026-11-27T11:00+03:00"}),
        _snap(fields={"auction_date": "2026-11-28T11:00+03:00"}),
    )
    assert _types(moved) == ["AUCTION_DATE_CHANGED"] and moved[0]["old_value"]


def test_vanished_date_is_not_an_event():
    old = _snap(fields={"auction_date": "2026-11-27T11:00+03:00"})
    assert diff_snapshots(old, _snap()) == []


def test_rebid_price_status_and_security():
    changes = diff_snapshots(
        _snap(),
        _snap(
            fields={"rebid_date": "2026-12-01T09:00+03:00", "price": "900000.00", "status": "cancelled", "currency": "RUB"},
            security={"Размер обеспечения заявки": "5%", "Размер обеспечения исполнения контракта": "10%"},
        ),
    )
    assert sorted(_types(changes)) == [
        "AMOUNT_CHANGED",
        "PROCEDURE_CANCELLED",
        "REBID_DATE_CHANGED",
        "SECURITY_CHANGED",
        "SECURITY_CHANGED",
    ]
    kinds = {c["data"]["security_kind"] for c in changes if c["event_type"] == "SECURITY_CHANGED"}
    assert kinds == {"APPLICATION", "CONTRACT"}  # заявка и контракт — отдельными событиями
    assert next(c for c in changes if c["event_type"] == "AMOUNT_CHANGED")["data"] == {"currency": "RUB"}
    # Число в другом написании — не изменение.
    assert diff_snapshots(_snap(), _snap(fields={"price": "1000000"})) == []


def test_other_status_is_general_status_event():
    changes = diff_snapshots(_snap(), _snap(fields={"status": "evaluation"}))
    assert _types(changes) == ["PROCEDURE_STATUS_CHANGED"]


def test_documents_classified_by_kind():
    old = _snap(
        documents={
            "u/contract1": {"name": "Проект контракта.docx", "class": "contract"},
            "u/tz1": {"name": "ТЗ.docx", "class": "tz_description"},
        }
    )
    new = _snap(
        documents={
            **old["documents"],
            "u/contract2": {"name": "Проект контракта (ред.2).docx", "class": "contract"},
            "u/tz2": {"name": "ТЗ.docx", "class": "tz_description"},
            "u/prot": {"name": "Протокол подведения итогов.pdf", "class": "protocol"},
            "u/prot2": {"name": "Протокол рассмотрения заявок.pdf", "class": "protocol"},
            "u/clar": {"name": "Разъяснения положений документации.docx", "class": "other"},
            "u/other": {"name": "Приложение 5.xlsx", "class": "other"},
            "u/same": {"name": "Приложение 5.xlsx", "class": "other"},
        }
    )
    by_url = {c["source_url"]: c for c in diff_snapshots(old, new)}
    assert by_url["u/contract2"]["event_type"] == "CONTRACT_CHANGED"
    assert by_url["u/tz2"]["event_type"] == "TECHNICAL_REQUIREMENT_CHANGED"
    assert by_url["u/tz2"]["note"] and "редакция" in by_url["u/tz2"]["note"]
    assert by_url["u/prot"]["event_type"] == "PROTOCOL_PUBLISHED"
    assert by_url["u/prot"]["data"]["is_final"] is True
    assert by_url["u/prot2"]["data"]["is_final"] is None  # итоговость по названию не определить
    assert by_url["u/clar"]["event_type"] == "CLARIFICATION_PUBLISHED"
    assert by_url["u/clar"]["old_value"] is None
    assert by_url["u/other"]["event_type"] == "DOCUMENT_ADDED"
    assert "u/contract1" not in by_url


def test_first_contract_is_added_document_not_new_edition():
    new = _snap(documents={"u/c": {"name": "Проект контракта.docx", "class": "contract"}})
    assert _types(diff_snapshots(_snap(), new)) == ["DOCUMENT_ADDED"]


def test_replaced_document_with_same_name():
    old = _snap(documents={"u/a": {"name": "Приложение.xlsx", "class": "other"}})
    new = _snap(documents={**old["documents"], "u/b": {"name": "Приложение.xlsx", "class": "other"}})
    (change,) = diff_snapshots(old, new)
    assert change["event_type"] == "DOCUMENT_REPLACED" and change["old_value"] == "Приложение.xlsx"


def test_disappeared_document_is_not_reported():
    old = _snap(documents={"u/a": {"name": "a.docx", "class": "other"}})
    assert diff_snapshots(old, _snap()) == []
    assert diff_snapshots(old, _snap(documents={"u/b": {"name": "b.docx", "class": "other"}})) == [
        c for c in diff_snapshots(old, _snap(documents={"u/b": {"name": "b.docx", "class": "other"}}))
        if c["event_type"] == "DOCUMENT_ADDED"
    ]


# --- с базой -----------------------------------------------------------------------------------


def _tender(db) -> Tender:
    source = Source(key=f"mon_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3261{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков",
        currency="RUB",
        price=Decimal("1000000"),
        status="collecting_bids",
    )
    db.add(tender)
    db.flush()
    return tender


def test_baseline_then_changes_become_events_with_chain(db_session, client, admin_user):
    tender = _tender(db_session)
    db_session.add(monitor.TenderMonitor(tender_id=tender.id))
    db_session.flush()

    assert monitor.check_tender(db_session, tender, refresh=False) == []  # исходный слепок
    assert monitor.get_monitor(db_session, tender.id).snapshot_at is not None

    tender.price = Decimal("900000")
    db_session.add(
        TenderDocument(
            tender_id=tender.id,
            file_name="Разъяснения.docx",
            source_url="https://example.test/clar.docx",
            document_class="other",
        )
    )
    db_session.flush()
    events = monitor.check_tender(db_session, tender, refresh=False)
    by_type = {e.event_type: e for e in events}
    assert set(by_type) == {"AMOUNT_CHANGED", "CLARIFICATION_PUBLISHED"}
    assert all(e.delivery == EventDelivery.PENDING.value for e in events)
    assert monitor.check_tender(db_session, tender, refresh=False) == []  # повтор — без дублей

    tender.price = Decimal("800000")
    db_session.flush()
    again = monitor.check_tender(db_session, tender, refresh=False)
    assert again[0].supersedes_event_id == by_type["AMOUNT_CHANGED"].event_id
    assert again[0].event_id != by_type["AMOUNT_CHANGED"].event_id


class _Receiver:
    def __init__(self, status_code: int = 202) -> None:
        self.bodies: list[dict] = []
        self.status_code = status_code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        if self.status_code >= 400:
            return httpx.Response(self.status_code, json={"ok": False, "error": {"message": "плохо"}})
        return httpx.Response(self.status_code, json={"ok": True, "result": {"deal_id": 1984}})


def _ready(db, admin, tender, *, with_deal: bool = True):
    bitrix_ai_service.update_settings(
        db, {"receiver_url": URL, "secret": SECRET, "push_enabled": True}, actor=admin
    )
    if with_deal:
        db.add(BitrixDealLink(tender_id=tender.id, deal_id=1984))
    db.add(monitor.TenderMonitor(tender_id=tender.id))
    db.flush()
    monitor.check_tender(db, tender, refresh=False)
    tender.application_end = None
    tender.price = Decimal("900000")
    db.add(
        TenderDocument(
            tender_id=tender.id,
            file_name="Протокол.pdf",
            source_url="https://example.test/p.pdf",
            document_class="protocol",
        )
    )
    db.flush()
    return monitor.check_tender(db, tender, refresh=False)


def test_send_payload_matches_portal_profile(db_session, client, admin_user):
    tender = _tender(db_session)
    tender.application_end = datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc)
    _ready(db_session, admin_user, tender)
    receiver = _Receiver()

    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))

    assert result["status"] == "sent" and result["sent"] == 2
    body = receiver.bodies[0]
    assert body["deal_id"] == 1984 and body["schema_version"] == 2
    # Раздел документов — только по событию о документе (протокол); анализ не трогается.
    assert list(body["sections"]) == ["documents"]
    (doc_row,) = body["sections"]["documents"]
    assert doc_row["type"] == "procurement_document" and doc_row["data"]["file_name"] == "Протокол.pdf"
    assert body["full_snapshot"] is False
    events = {e["event_type"]: e for e in body["events"]}
    assert set(events) == {"AMOUNT_CHANGED", "PROTOCOL_PUBLISHED"}
    amount = events["AMOUNT_CHANGED"]
    # НМЦК — числом, оба значения присутствуют, валюта в data; дата — с секундами и поясом.
    assert amount["old_value"] == 1000000 and amount["new_value"] == 900000
    assert amount["data"] == {"currency": "RUB"} and amount["scope"] == "PROCEDURE"
    protocol = events["PROTOCOL_PUBLISHED"]
    assert protocol["old_value"] is None and "old_value" in protocol
    assert protocol["new_value"] == "Протокол.pdf" and protocol["source_url"] == "https://example.test/p.pdf"
    assert "lot_number" not in protocol and "effective_at" not in protocol
    assert protocol["event_id"].startswith("sova-evt-")
    # В сделку — только поля самих событий: сумма и валюта; результат и прочее не трогаются.
    assert set(body["deal_fields"]) == {"amount", "currency"}
    assert body["deal_fields"]["amount"] == 900000
    assert {e.delivery for e in db_session.query(TenderEvent).filter_by(tender_id=tender.id)} == {"sent"}
    assert tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))["status"] == "empty"
    assert len(receiver.bodies) == 1


def test_dates_are_rfc3339_with_seconds():
    from app.services.tender_event_service import _typed

    assert _typed("AUCTION_DATE_CHANGED", "2026-11-27T11:00+03:00") == "2026-11-27T11:00:00+03:00"
    assert _typed("AMOUNT_CHANGED", "12500000.50") == 12500000.5
    assert _typed("AMOUNT_CHANGED", None) is None
    assert _typed("SECURITY_CHANGED", "5%") == "5%"


def test_amount_not_sent_to_deal_after_win(db_session, client, admin_user, monkeypatch):
    tender = _tender(db_session)
    _ready(db_session, admin_user, tender)
    monkeypatch.setattr(bitrix_ai_service, "_result_status", lambda db, t: "WIN")
    receiver = _Receiver()

    tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))

    body = receiver.bodies[0]
    assert "amount" not in body["deal_fields"] and "currency" not in body["deal_fields"]
    assert any(e["event_type"] == "AMOUNT_CHANGED" for e in body["events"])  # журнал остаётся


def test_unagreed_event_is_held_until_type_agreed(db_session, client, admin_user, monkeypatch):
    tender = _tender(db_session)
    monkeypatch.setitem(monitor.EVENT_TYPES, "AMOUNT_CHANGED", False)
    _ready(db_session, admin_user, tender)
    receiver = _Receiver()

    tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))
    states = {e.event_type: e.delivery for e in db_session.query(TenderEvent).filter_by(tender_id=tender.id)}
    assert states == {"PROTOCOL_PUBLISHED": "sent", "AMOUNT_CHANGED": "held"}

    monkeypatch.setitem(monitor.EVENT_TYPES, "AMOUNT_CHANGED", True)
    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))
    assert result["sent"] == 1
    assert receiver.bodies[1]["events"][0]["event_type"] == "AMOUNT_CHANGED"


def test_resend_keeps_event_content_identical(db_session, client, admin_user):
    """Тот же event_id с другим содержимым портал отклоняет (409): повтор не должен менять тело."""

    tender = _tender(db_session)
    _ready(db_session, admin_user, tender)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет связи")

    tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(down))
    receiver = _Receiver()
    tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))
    first = {e.event_id: e for e in db_session.query(TenderEvent).filter_by(tender_id=tender.id)}
    sent = {e["event_id"]: e for e in receiver.bodies[0]["events"]}
    for event_id, payload in sent.items():
        assert payload == tender_event_service.event_out(first[event_id])


def test_no_send_without_deal_or_when_disabled(db_session, client, admin_user):
    tender = _tender(db_session)
    _ready(db_session, admin_user, tender, with_deal=False)
    receiver = _Receiver()
    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))
    assert result["status"] == "skipped" and "Загрузить в Битрикс" in result["detail"]
    assert receiver.bodies == []

    bitrix_ai_service.update_settings(db_session, {"push_enabled": False}, actor=admin_user)
    db_session.add(BitrixDealLink(tender_id=tender.id, deal_id=1984))
    db_session.flush()
    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))
    assert result["status"] == "skipped" and "выключена" in result["detail"]


def test_portal_rejection_is_not_retried_automatically(db_session, client, admin_user):
    tender = _tender(db_session)
    _ready(db_session, admin_user, tender)
    receiver = _Receiver(400)

    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))
    assert result["status"] == "error"
    event = db_session.query(TenderEvent).filter_by(tender_id=tender.id, event_type="PROTOCOL_PUBLISHED").one()
    assert event.delivery == EventDelivery.ERROR.value and "HTTP 400" in event.error
    assert tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))["status"] == "empty"

    assert tender_event_service.retry_errors(db_session, tender) == 2


def test_network_failure_keeps_events_queued(db_session, client, admin_user):
    tender = _tender(db_session)
    _ready(db_session, admin_user, tender)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет связи")

    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(down))
    assert result["status"] == "error"
    event = db_session.query(TenderEvent).filter_by(tender_id=tender.id, event_type="PROTOCOL_PUBLISHED").one()
    assert event.delivery == EventDelivery.PENDING.value


def test_api_state_and_guard(client, admin_token, db_session):
    headers = {"Authorization": f"Bearer {admin_token}"}
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        tender = _tender(db)
        db.commit()
        tender_id = str(tender.id)
    state = client.get(f"/tenders/{tender_id}/monitoring", headers=headers)
    assert state.status_code == 200
    assert state.json()["enabled"] is False and state.json()["events"] == []
    # Проверка закупки, не поставленной под наблюдение, — понятный отказ, а не 500.
    assert client.post(f"/tenders/{tender_id}/monitoring/check", headers=headers).status_code == 409
    assert client.get(f"/tenders/{uuid.uuid4()}/monitoring", headers=headers).status_code == 404
    assert client.get("/monitoring", headers=headers).status_code == 200
