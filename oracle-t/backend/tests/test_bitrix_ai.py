"""Тесты пакета ИИ-анализа в обработчик Bitrix24 `receive.php` (20.10.2026).

В портал тесты не ходят: `httpx.MockTransport` играет роль обработчика и проверяет запрос так
же, как он, — HMAC от точного тела, совпадение `request_id` в заголовке и теле. Проверяется
то, что на боевом портале обошлось бы дорого: отправка при выключенном выключателе, повтор с
другим телом (портал ответит 409), потеря id созданной сделки, секрет в теле или журнале.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.models.analysis import Requirement
from app.models.bitrix_deal import BitrixDealLink
from app.models.log import Log
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.services import bitrix_ai_service as service

URL = "https://b24.example.test/local/tools/tender_ai_analysis/integration/receive.php"
SECRET = "0123456789abcdef0123456789abcdef"


def _tender(db) -> Tender:
    source = Source(
        key=f"b24ai_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis"
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3261{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков электрической энергии",
        currency="RUB",
        price=Decimal("1250000.50"),
        customer_name="ПАО «Россети Сибирь»",
        procurement_method="Запрос котировок",
        source_url="https://zakupki.gov.ru/notice/1",
        tender_type="supply_only",
        application_end=datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc),
    )
    db.add(tender)
    db.flush()
    db.add(
        TenderCard(
            tender_id=tender.id,
            payload={
                "sections": [
                    {"title": "Сведения об организаторе", "fields": [["ИНН", "7700000000"]]},
                    {"title": "Сведения о заказчике", "fields": [["ИНН", "2460069527"]]},
                ]
            },
        )
    )
    db.add(
        Requirement(
            tender_id=tender.id,
            text="Класс точности по активной энергии не ниже 1",
            criticality="critical",
            kind="product",
        )
    )
    db.add(
        Requirement(
            tender_id=tender.id,
            text="Опыт поставок не менее трёх договоров",
            criticality="important",
            kind="participant",
        )
    )
    db.flush()
    return tender


def _configure(db, admin, *, enabled: bool = True) -> None:
    service.update_settings(
        db, {"receiver_url": URL, "secret": SECRET, "push_enabled": enabled}, actor=admin
    )


class FakeReceiver:
    """Обработчик в памяти: проверяет подпись и повторы, заводит сделку при `deal_id: 0`."""

    def __init__(self, *, fail_with: int | None = None) -> None:
        self.requests: list[dict] = []
        self.seen: dict[str, bytes] = {}
        self.fail_with = fail_with
        self.next_deal = 1984

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        ts = request.headers["X-Tender-AI-Timestamp"]
        rid = request.headers["X-Tender-AI-Request-Id"]
        expected = hmac.new(
            SECRET.encode(), ts.encode() + b"." + rid.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        payload = json.loads(body)
        self.requests.append({"headers": dict(request.headers), "payload": payload, "body": body})
        if request.headers["X-Tender-AI-Signature"] != expected:
            return httpx.Response(401, json={"ok": False, "error": {"message": "bad signature"}})
        if payload["request_id"] != rid:
            return httpx.Response(400, json={"ok": False, "error": {"message": "request_id mismatch"}})
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"ok": False, "error": {"message": "boom"}})
        if rid in self.seen:
            if self.seen[rid] != body:
                return httpx.Response(409, json={"ok": False, "error": {"message": "conflict"}})
            return httpx.Response(200, json={"ok": True, "result": {"accepted": True, "duplicate": True, "deal_id": payload["deal_id"] or self.next_deal}})
        self.seen[rid] = body
        created = payload["deal_id"] == 0
        deal_id = self.next_deal if created else payload["deal_id"]
        return httpx.Response(
            202,
            json={
                "ok": True,
                "result": {
                    "accepted": True,
                    "duplicate": False,
                    "request_id": rid,
                    "deal_id": deal_id,
                    "deal_created": created,
                    "items_received": sum(len(v) for v in payload["sections"].values()),
                },
            },
        )


def test_payload_shape_matches_portal_contract(db_session, client, admin_user):
    tender = _tender(db_session)
    payload = service.build_payload(db_session, tender)

    assert payload["schema_version"] == 2
    assert payload["deal_id"] == 0
    assert set(payload["sections"]) == set(service.SECTIONS)
    # ИНН — заказчика, а не организатора, хотя организатор в карточке раньше.
    assert payload["lookup"] == {
        "procedure_number": tender.external_id,
        "lot_number": "1",
        "customer_inn": "2460069527",
        "customer_name": "ПАО «Россети Сибирь»",
        "procedure_url": "https://zakupki.gov.ru/notice/1",
    }
    fields = payload["deal_fields"]
    assert fields["amount"] == 1250000.5
    assert fields["tender_types"] == ["IPU"]
    assert fields["direction"] == "ELECTRICITY"
    assert fields["record_type"] == "SINGLE"
    assert fields["result_status"] == "PENDING"
    # Даты — ISO с часовым поясом, по Москве.
    assert fields["application_end"] == "2026-10-30T10:00:00+03:00"
    # Пустые значения не передаются вовсе.
    assert "delivery_region" not in fields

    tz = payload["sections"]["technical_specification"]
    prep = payload["sections"]["preparation"]
    # Первой идёт краткая выжимка (портал 1.4.1), затем требования.
    assert tz[0]["type"] == "technical_summary"
    tz = tz[1:]
    assert [row["type"] for row in tz] == ["requirement"]
    assert all(row["status"] for row in tz + prep)
    assert [row["type"] for row in prep] == ["qualification"]
    assert tz[0]["data"]["criticality"] == "Критическое"
    assert all(row["sort"] and row["key"] and row["title"] for row in tz + prep)


def test_row_keys_survive_reanalysis(db_session, client, admin_user):
    """Требования пересоздаются при повторном разборе — ключ строки не должен меняться,
    иначе портал заведёт дубль вместо обновления."""

    tender = _tender(db_session)
    before = {row["key"] for row in service.build_sections(db_session, tender)["technical_specification"]}
    for requirement in db_session.scalars(select(Requirement).where(Requirement.tender_id == tender.id)):
        db_session.delete(requirement)
    db_session.flush()
    db_session.add(
        Requirement(
            tender_id=tender.id,
            text="Класс  точности по активной энергии не ниже 1",
            criticality="critical",
            kind="product",
        )
    )
    db_session.flush()
    after = {row["key"] for row in service.build_sections(db_session, tender)["technical_specification"]}
    assert before == after


def test_send_signs_exact_body_and_stores_created_deal(db_session, client, admin_user):
    tender = _tender(db_session)
    _configure(db_session, admin_user)
    receiver = FakeReceiver()

    result = service.send(db_session, tender, actor=admin_user, transport=httpx.MockTransport(receiver))

    assert result["accepted"] is True
    assert result["deal_id"] == 1984 and result["deal_created"] is True
    assert result["http_status"] == 202
    assert db_session.get(BitrixDealLink, tender.id).deal_id == 1984
    sent = receiver.requests[0]
    assert sent["headers"]["content-type"] == "application/json"
    assert SECRET.encode() not in sent["body"]

    # Второй пакет — по сохранённому id сделки, с новыми request_id и analysis_id.
    second = service.send(db_session, tender, actor=admin_user, transport=httpx.MockTransport(receiver))
    payload = receiver.requests[1]["payload"]
    assert payload["deal_id"] == 1984
    assert second["deal_created"] is False
    assert payload["request_id"] != receiver.requests[0]["payload"]["request_id"]
    assert payload["analysis_id"] != receiver.requests[0]["payload"]["analysis_id"]


def test_send_refused_while_disabled(db_session, client, admin_user):
    tender = _tender(db_session)
    _configure(db_session, admin_user, enabled=False)
    receiver = FakeReceiver()

    with pytest.raises(service.BitrixAiPushDisabledError):
        service.send(db_session, tender, actor=admin_user, transport=httpx.MockTransport(receiver))
    assert receiver.requests == []


def test_retry_resends_same_bytes_after_server_error(db_session, client, admin_user):
    tender = _tender(db_session)
    _configure(db_session, admin_user)
    receiver = FakeReceiver(fail_with=500)

    failed = service.send(db_session, tender, actor=admin_user, transport=httpx.MockTransport(receiver))
    assert failed["accepted"] is False and failed["retryable"] is True
    assert "HTTP 500" in failed["error"]

    receiver.fail_with = None
    retried = service.retry(
        db_session, uuid.UUID(failed["id"]), actor=admin_user, transport=httpx.MockTransport(receiver)
    )
    assert retried["accepted"] is True and retried["attempts"] == 2
    assert receiver.requests[0]["body"] == receiver.requests[1]["body"]
    assert receiver.requests[0]["payload"]["request_id"] == retried["request_id"]


def test_rejected_package_is_not_retried(db_session, client, admin_user):
    tender = _tender(db_session)
    _configure(db_session, admin_user)
    failed = service.send(
        db_session, tender, actor=admin_user, transport=httpx.MockTransport(FakeReceiver(fail_with=403))
    )
    assert failed["retryable"] is False
    assert "тестовой тендерной воронке" in failed["error"]
    with pytest.raises(service.BitrixAiError):
        service.retry(db_session, uuid.UUID(failed["id"]), actor=admin_user)


def test_secret_never_leaves_settings(db_session, client, admin_user):
    _configure(db_session, admin_user)
    out = service.settings_out(db_session)
    assert out["secret_set"] is True
    assert SECRET not in json.dumps(out)
    assert out["secret_hint"] == "••••cdef"
    logs = db_session.scalars(select(Log.details).where(Log.action == "update_bitrix24_ai_settings"))
    assert all(SECRET not in (details or "") for details in logs)


def test_settings_reject_non_php_url(db_session, client, admin_user):
    with pytest.raises(service.BitrixAiError):
        service.update_settings(db_session, {"receiver_url": "http://b24.example.test/"}, actor=admin_user)


def test_card_upload_only_for_reviewed_tender(db_session, client, admin_user):
    """Кнопка на странице тендера: «Новая» и «Отклонён» не выгружаются, «На проверке» —
    да, повторное нажатие обновляет ту же сделку."""

    tender = _tender(db_session)
    _configure(db_session, admin_user)
    receiver = FakeReceiver()
    transport = httpx.MockTransport(receiver)

    state = service.tender_state(db_session, tender)
    assert state["can_upload"] is False and "На проверке" in state["reason"]
    with pytest.raises(service.BitrixAiError):
        service.upload_from_card(db_session, tender, actor=admin_user, transport=transport)
    assert receiver.requests == []

    tender.stage = "under_review"
    db_session.flush()
    first = service.upload_from_card(db_session, tender, actor=admin_user, transport=transport)
    assert first["deal_id"] == 1984
    assert first["deal_url"] == "https://b24.example.test/crm/deal/details/1984/"
    assert first["last_request"]["accepted"] is True

    second = service.upload_from_card(db_session, tender, actor=admin_user, transport=transport)
    assert receiver.requests[1]["payload"]["deal_id"] == 1984
    assert second["last_request"]["deal_created"] is False

    tender.stage = "rejected"
    db_session.flush()
    assert "отклонена" in service.tender_state(db_session, tender)["reason"]


def test_card_upload_reports_disabled_push(db_session, client, admin_user):
    tender = _tender(db_session)
    tender.stage = "under_review"
    _configure(db_session, admin_user, enabled=False)
    state = service.tender_state(db_session, tender)
    assert state["can_upload"] is False
    assert "выключена" in state["reason"]


def test_duplicate_requirement_keys_get_suffix(db_session, client, admin_user):
    """Два требования, отличающихся одним двоеточием, дают один нормализованный текст —
    портал отклонил такой пакет целиком (HTTP 400, 20.10.2026)."""

    tender = _tender(db_session)
    for text in ("Интерфейсы связи GSM/NB-IoT", "Интерфейсы связи: GSM/NB-IoT"):
        db_session.add(
            Requirement(
                tender_id=tender.id,
                text=text,
                normalized_text="интерфейсы связи gsm/nb-iot",
                criticality="important",
                kind="product",
            )
        )
    db_session.flush()
    rows = service.build_sections(db_session, tender)["technical_specification"]
    keys = [(row["type"], row["key"]) for row in rows]
    assert len(keys) == len(set(keys))
    assert any(key.endswith("-2") for _, key in keys)


def test_procedure_dates_come_from_eis_card(db_session, client, admin_user):
    """Первая выгрузка 20.10.2026: начало подачи и подведение итогов в сделке были пусты, а
    окончание стояло 12:00 вместо 05:00 МСК — точные значения есть только в карточке ЕИС."""

    tender = _tender(db_session)
    card = db_session.get(TenderCard, tender.id)
    card.payload = {
        **card.payload,
        "sections": card.payload["sections"]
        + [
            {
                "title": "Порядок проведения процедуры",
                "fields": [
                    ["Дата начала срока подачи заявок", "18.09.2026 (МСК+4)"],
                    ["Дата и время окончания срока подачи заявок (по местному времени заказчика)", "29.09.2026 09:00 (МСК+4)"],
                    ["Дата подведения итогов", "01.10.2026"],
                    ["Дата начала срока подачи дополнительных ценовых предложений", "03.10.2026 10:00 (МСК)"],
                ],
            }
        ],
    }
    db_session.flush()
    fields = service.build_deal_fields(db_session, tender)
    assert fields["application_start"] == "2026-09-18T00:00:00+03:00"
    assert fields["application_end"] == "2026-09-29T05:00:00+03:00"
    assert fields["result_date"] == "2026-10-01T00:00:00+03:00"
    assert fields["rebid_date"] == "2026-10-03T10:00:00+03:00"
    assert "auction_date" not in fields
