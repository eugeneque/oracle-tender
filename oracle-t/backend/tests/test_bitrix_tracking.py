"""Наблюдение за выгруженными в Bitrix24 закупками (12.11.2026, раздел 100 ARCHITECTURE.md).

Сеть не используется: проверки идут с `refresh=False`, письмо перехватывается подменой
`notification_service.notify_tender_changed`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.ai_profile import AiProfileScore
from app.models.bitrix_deal import BitrixDealLink
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.models.tender_event import TenderMonitor
from app.services import notification_service, tender_monitor_service as monitor


def _tender(db, **extra) -> Tender:
    source = Source(key=f"trk_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3262{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков",
        currency="RUB",
        price=Decimal("1000000"),
        status="collecting_bids",
        **extra,
    )
    db.add(tender)
    db.flush()
    return tender


def _doc(db, tender, name="ТЗ.docx", url=None, klass="other"):
    db.add(
        TenderDocument(
            tender_id=tender.id,
            file_name=name,
            source_url=url or f"https://example.test/{uuid.uuid4().hex}.docx",
            document_class=klass,
        )
    )
    db.flush()


def test_pushed_tenders_are_enrolled_and_manual_off_is_respected(db_session):
    pushed = _tender(db_session)
    switched_off = _tender(db_session)
    not_pushed = _tender(db_session)
    long_gone = _tender(db_session, application_end=datetime.now(timezone.utc) - timedelta(days=200))
    for tender in (pushed, switched_off, long_gone):
        db_session.add(BitrixDealLink(tender_id=tender.id, deal_id=100))
    db_session.add(TenderMonitor(tender_id=switched_off.id, enabled=False))
    db_session.flush()

    monitor.enroll_bitrix_tenders(db_session)

    state = monitor.get_monitor(db_session, pushed.id)
    assert state is not None and state.enabled and state.auto and state.snapshot is not None
    assert monitor.get_monitor(db_session, switched_off.id).enabled is False
    assert monitor.get_monitor(db_session, not_pushed.id) is None
    assert monitor.get_monitor(db_session, long_gone.id) is None


def test_auto_monitor_stops_when_cancelled(db_session):
    tender = _tender(db_session)
    db_session.add(BitrixDealLink(tender_id=tender.id, deal_id=100))
    db_session.flush()
    monitor.enroll_bitrix_tenders(db_session)
    tender.status = "cancelled"
    db_session.flush()
    monitor.enroll_bitrix_tenders(db_session)
    assert monitor.get_monitor(db_session, tender.id).enabled is False


def test_first_document_download_after_enrolment_is_not_a_flood(db_session):
    tender = _tender(db_session)
    monitor.ensure_for_bitrix(db_session, tender)
    _doc(db_session, tender, "Извещение.pdf")
    _doc(db_session, tender, "ТЗ.docx", klass="tz_description")
    assert monitor.check_tender(db_session, tender, refresh=False) == []
    _doc(db_session, tender, "Разъяснения.docx")
    events = monitor.check_tender(db_session, tender, refresh=False)
    assert [e.event_type for e in events] == ["CLARIFICATION_PUBLISHED"]


def test_important_change_marks_analysis_outdated_and_new_score_clears_it(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(
        notification_service,
        "notify_tender_changed",
        lambda db, tender, events, analysis_outdated=False: sent.append((len(events), analysis_outdated)),
    )
    tender = _tender(db_session)
    _doc(db_session, tender)
    score = AiProfileScore(
        tender_id=tender.id, is_current=True, calculated_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )
    db_session.add(score)
    monitor.ensure_for_bitrix(db_session, tender)

    # Перенос аукциона на оценку соответствия не влияет: письмо есть, пометки нет.
    from app.models.tender_card import TenderCard  # noqa: F401 - карточки нет, дата из неё не берётся

    tender.application_end = datetime.now(timezone.utc) + timedelta(days=5)
    db_session.flush()
    assert [e.event_type for e in monitor.check_tender(db_session, tender, refresh=False)] == [
        "APPLICATION_END_CHANGED"
    ]
    assert monitor.analysis_outdated_events(db_session, tender) == []
    assert sent[-1] == (1, False)

    tender.price = Decimal("900000")
    db_session.flush()
    monitor.check_tender(db_session, tender, refresh=False)
    outdated = monitor.analysis_outdated_events(db_session, tender)
    assert [e.event_type for e in outdated] == ["AMOUNT_CHANGED"]
    assert sent[-1] == (1, True)

    score.is_current = False
    db_session.add(AiProfileScore(tender_id=tender.id, is_current=True, calculated_at=datetime.now(timezone.utc)))
    db_session.flush()
    assert monitor.analysis_outdated_events(db_session, tender) == []


def test_unseen_events_per_user(db_session, client, admin_user):
    tender = _tender(db_session)
    monitor.ensure_for_bitrix(db_session, tender)
    tender.price = Decimal("900000")
    db_session.flush()
    monitor.check_tender(db_session, tender, refresh=False)
    assert monitor.unseen_counts(db_session, admin_user).get(tender.id) == 1
    monitor.mark_seen(db_session, admin_user, tender.id)
    assert tender.id not in monitor.unseen_counts(db_session, admin_user)


def test_tender_changed_email_lists_changes(db_session, monkeypatch):
    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = True
    settings.smtp_host = "smtp.example.test"
    settings.smtp_username = "sova@example.test"
    settings.recipients = "team@example.test"
    db_session.flush()
    letters = []
    monkeypatch.setattr(
        notification_service, "_send_email", lambda s, to, subject, body, html=None: letters.append((subject, body))
    )
    tender = _tender(db_session)
    monitor.ensure_for_bitrix(db_session, tender)
    tender.price = Decimal("900000")
    db_session.flush()
    monitor.check_tender(db_session, tender, refresh=False)
    assert len(letters) == 1
    subject, body = letters[0]
    assert tender.external_id in subject
    assert "Изменилась НМЦК" in body and "Было: 1 000 000 RUB" in body and "Стало: 900 000 RUB" in body


def test_api_unseen_and_score_outdated(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        tender = _tender(db)
        db.add(AiProfileScore(tender_id=tender.id, is_current=True, calculated_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        monitor.ensure_for_bitrix(db, tender)
        db.commit()
        tender.price = Decimal("800000")
        db.commit()
        monitor.check_tender(db, tender, refresh=False)
        tender_id = str(tender.id)

    assert client.get("/monitoring/unseen", headers=headers).json().get(tender_id) == 1
    state = client.get(f"/tenders/{tender_id}/monitoring", headers=headers).json()
    assert state["auto"] is True and state["events"][0]["is_new"] and state["events"][0]["affects_analysis"]
    score = client.get(f"/tenders/{tender_id}/ai-score", headers=headers).json()
    assert [e["event_type"] for e in score["outdated_events"]] == ["AMOUNT_CHANGED"]
    assert client.post(f"/tenders/{tender_id}/monitoring/seen", headers=headers).status_code == 204
    assert tender_id not in client.get("/monitoring/unseen", headers=headers).json()
