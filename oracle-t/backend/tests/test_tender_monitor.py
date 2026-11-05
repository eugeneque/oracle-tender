"""Отслеживание изменений по закупкам и события для Bitrix24 (05.11.2026).

Сеть не используется: сравнение слепков — чистая функция, проверка закупки идёт с
`refresh=False` (площадки не опрашиваются), портал играет `httpx.MockTransport`.
"""

from __future__ import annotations

import json
import uuid
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
    assert _types(moved) == ["DEADLINE_CHANGED"]
    assert moved[0]["old_value"] == "2026-11-20T10:00+03:00"
    assert moved[0]["new_value"] == "2026-11-25T10:00+03:00"


def test_auction_first_scheduled_then_changed():
    first = diff_snapshots(_snap(), _snap(fields={"auction_date": "2026-11-27T11:00+03:00"}))
    assert _types(first) == ["AUCTION_SCHEDULED"] and first[0]["old_value"] is None
    moved = diff_snapshots(
        _snap(fields={"auction_date": "2026-11-27T11:00+03:00"}),
        _snap(fields={"auction_date": "2026-11-28T11:00+03:00"}),
    )
    assert _types(moved) == ["AUCTION_CHANGED"]


def test_vanished_date_is_not_an_event():
    old = _snap(fields={"auction_date": "2026-11-27T11:00+03:00"})
    assert diff_snapshots(old, _snap()) == []


def test_rebid_price_status_and_security():
    changes = diff_snapshots(
        _snap(),
        _snap(
            fields={"rebid_date": "2026-12-01T09:00+03:00", "price": "900000.00", "status": "cancelled"},
            security={"Размер обеспечения заявки": "5%"},
        ),
    )
    assert set(_types(changes)) == {"REBID_SCHEDULED", "PRICE_CHANGED", "PROCEDURE_CANCELLED", "SECURITY_CHANGED"}
    # Число в другом написании — не изменение.
    assert diff_snapshots(_snap(), _snap(fields={"price": "1000000"})) == []


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
            "u/prot": {"name": "Протокол рассмотрения заявок.pdf", "class": "protocol"},
            "u/clar": {"name": "Разъяснения положений документации.docx", "class": "other"},
            "u/other": {"name": "Приложение 5.xlsx", "class": "other"},
        }
    )
    by_url = {c["source_url"]: c for c in diff_snapshots(old, new)}
    assert by_url["u/contract2"]["event_type"] == "CONTRACT_CHANGED"
    assert by_url["u/tz2"]["event_type"] == "TZ_CHANGED"
    assert by_url["u/tz2"]["note"] and "редакция" in by_url["u/tz2"]["note"]
    assert by_url["u/prot"]["event_type"] == "PROTOCOL_PUBLISHED"
    assert by_url["u/clar"]["event_type"] == "CLARIFICATION_RECEIVED"
    assert by_url["u/other"]["event_type"] == "DOCUMENT_CHANGED"
    assert "u/contract1" not in by_url


def test_first_contract_is_added_document_not_new_edition():
    new = _snap(documents={"u/c": {"name": "Проект контракта.docx", "class": "contract"}})
    assert _types(diff_snapshots(_snap(), new)) == ["DOCUMENT_CHANGED"]


def test_empty_document_list_is_failure_not_removal():
    old = _snap(documents={"u/a": {"name": "a.docx", "class": "other"}})
    assert diff_snapshots(old, _snap()) == []
    removed = diff_snapshots(old, _snap(documents={"u/b": {"name": "b.docx", "class": "other"}}))
    assert "Документ убран" in removed[0]["title"] or any("убран" in c["title"] for c in removed)


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
    assert set(by_type) == {"PRICE_CHANGED", "CLARIFICATION_RECEIVED"}
    assert by_type["PRICE_CHANGED"].delivery == EventDelivery.HELD.value  # код не согласован
    assert by_type["CLARIFICATION_RECEIVED"].delivery == EventDelivery.PENDING.value
    assert monitor.check_tender(db_session, tender, refresh=False) == []  # повтор — без дублей

    tender.price = Decimal("800000")
    db_session.flush()
    again = monitor.check_tender(db_session, tender, refresh=False)
    assert again[0].supersedes_event_id == by_type["PRICE_CHANGED"].event_id
    assert again[0].event_id != by_type["PRICE_CHANGED"].event_id


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


def test_send_only_agreed_events_without_sections(db_session, client, admin_user):
    tender = _tender(db_session)
    _ready(db_session, admin_user, tender)
    receiver = _Receiver()

    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))

    assert result["status"] == "sent" and result["sent"] == 1
    body = receiver.bodies[0]
    assert body["sections"] == {} and body["deal_id"] == 1984
    (event,) = body["events"]
    assert event["event_type"] == "PROTOCOL_PUBLISHED" and event["scope"] == "PROCEDURE"
    assert "lot_number" not in event and "old_value" not in event
    assert event["event_id"].startswith("sova-evt-")
    states = {e.event_type: e.delivery for e in db_session.query(TenderEvent).filter_by(tender_id=tender.id)}
    assert states == {"PROTOCOL_PUBLISHED": "sent", "PRICE_CHANGED": "held"}
    # Нечего отправлять — второй запрос не уходит.
    assert tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))["status"] == "empty"
    assert len(receiver.bodies) == 1


def test_held_event_goes_out_once_type_is_agreed(db_session, client, admin_user, monkeypatch):
    tender = _tender(db_session)
    _ready(db_session, admin_user, tender)
    receiver = _Receiver()
    tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))

    monkeypatch.setitem(monitor.EVENT_TYPES, "PRICE_CHANGED", True)
    result = tender_event_service.send_pending(db_session, tender, transport=httpx.MockTransport(receiver))

    assert result["sent"] == 1
    assert receiver.bodies[1]["events"][0]["event_type"] == "PRICE_CHANGED"


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

    assert tender_event_service.retry_errors(db_session, tender) == 1


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
