"""Тесты отправки тендеров сделками в Bitrix24 (08.10.2026).

В портал тесты не ходят: `BitrixClient` получает `httpx.MockTransport`, который записывает
вызовы и отвечает как портал. Проверяется то, что на боевом портале обошлось бы дорого:
запись в CRM при выключенной отправке, дубль сделки при повторной отправке, возврат
сдвинутой менеджером сделки на первую стадию, токен вебхука в тексте ошибки или в ответе API.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.models.bitrix_deal import BitrixDealLink
from app.models.source import Source
from app.models.tender import Tender
from app.services import bitrix_client, bitrix_deal_service
from app.services.bitrix_client import BitrixClient, BitrixError, normalize_webhook

WEBHOOK = "https://b24.example.test/rest/4/secrettoken123/"


def _tender(db, **overrides) -> Tender:
    source = Source(
        key=f"b24_{uuid.uuid4().hex[:8]}",
        name="ЭТП ГПБ",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    fields = {
        "title": "Поставка приборов учёта электрической энергии",
        "currency": "RUB",
        "price": Decimal("1234567.89"),
        "customer_name": 'ПАО "Россети"',
        "organizer_name": "АО «Организатор»",
        "procurement_method": "Запрос предложений",
        "source_url": "https://example.test/procedure/1",
        "publish_date": date(2026, 10, 1),
        "application_end": datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc),
        **overrides,
    }
    tender = Tender(source_id=source.id, external_id=f"B24-{uuid.uuid4().hex[:6]}", **fields)
    db.add(tender)
    db.flush()
    return tender


class FakePortal:
    """Портал в памяти: сделки, стадии воронки и журнал вызовов."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.deals: dict[int, dict] = {}
        self.next_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        params = json.loads(request.content or b"{}")
        self.calls.append((method, params))
        if method == "crm.status.list":
            return httpx.Response(
                200,
                json={"result": [
                    {"STATUS_ID": "C5:PRE_PUBLICATION", "NAME": "Подготовка до публикации"},
                    {"STATUS_ID": "C5:PARSING", "NAME": "Парсинг опубликованных тендеров"},
                ]},
            )
        if method == "crm.deal.fields":
            keys = [f.key for f in bitrix_deal_service.DEAL_FIELDS][1:]  # первого поля «нет»
            return httpx.Response(200, json={"result": {key: {} for key in ["TITLE", *keys]}})
        if method == "crm.deal.list":
            origin = params["filter"]["ORIGIN_ID"]
            found = [{"ID": str(i)} for i, d in self.deals.items() if d.get("ORIGIN_ID") == origin]
            return httpx.Response(200, json={"result": found, "total": len(found)})
        if method == "crm.deal.add":
            deal_id = self.next_id
            self.next_id += 1
            self.deals[deal_id] = dict(params["fields"])
            return httpx.Response(200, json={"result": deal_id})
        if method == "crm.deal.update":
            self.deals[int(params["id"])].update(params["fields"])
            return httpx.Response(200, json={"result": True})
        return httpx.Response(400, json={"error": "ERROR_METHOD_NOT_FOUND"})

    def written(self) -> list[str]:
        return [m for m, _ in self.calls if m in {"crm.deal.add", "crm.deal.update"}]


@pytest.fixture()
def portal(monkeypatch):
    fake = FakePortal()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(
        bitrix_deal_service, "BitrixClient", lambda webhook: BitrixClient(webhook, transport=transport)
    )
    return fake


@pytest.fixture()
def configured(db_session, admin_user):
    bitrix_deal_service.update_settings(db_session, {"webhook_url": WEBHOOK}, actor=admin_user)


class TestWebhook:
    def test_normalizes_trailing_slash(self):
        assert normalize_webhook("https://b24.example.test/rest/4/abc123") == (
            "https://b24.example.test/rest/4/abc123/"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "https://b24.example.test/rest/4/abc123/profile.json",  # с методом на конце
            "http://b24.example.test/rest/4/abc123/",  # без https
            "b24.example.test",
            "",
        ],
    )
    def test_rejects_non_webhook(self, value):
        with pytest.raises(bitrix_client.BitrixWebhookFormatError):
            normalize_webhook(value)

    def test_error_text_has_no_token(self):
        def handler(_request):
            return httpx.Response(401, json={"error": "NO_AUTH_FOUND", "error_description": "x"})

        with BitrixClient(WEBHOOK, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BitrixError) as exc_info:
                client.call("crm.deal.fields")
        assert "secrettoken123" not in str(exc_info.value)

    def test_rate_limit_is_retried(self, monkeypatch):
        monkeypatch.setattr(bitrix_client.time, "sleep", lambda _s: None)
        answers = iter([
            httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED", "error_description": "Too many"}),
            httpx.Response(200, json={"result": {"ok": True}}),
        ])
        with BitrixClient(WEBHOOK, transport=httpx.MockTransport(lambda _r: next(answers))) as client:
            assert client.call("crm.deal.fields") == {"ok": True}


class TestDealFields:
    def test_portal_formats(self, db_session):
        tender = _tender(db_session)
        fields = bitrix_deal_service.build_deal_fields(db_session, tender)

        assert fields["TITLE"].startswith(f"Тендер {tender.external_id}: ")
        assert fields["OPPORTUNITY"] == "1234567.89"
        # Поле «деньги» — строкой «сумма|валюта».
        assert fields["UF_CRM_1784720624836"] == "1234567.89|RUB"
        # Окончание приёма заявок — со смещением, иначе портал прочтёт UTC как своё время.
        assert fields["UF_CRM_1783503179"] == "2026-10-20T07:00:00+00:00"
        assert fields["UF_CRM_1783509007461"] == "2026-10-01"
        assert fields["ORIGIN_ID"] == str(tender.id)
        # Воронку и стадию задаёт только создание.
        assert "STAGE_ID" not in fields and "CATEGORY_ID" not in fields

    def test_empty_values_are_not_sent(self, db_session):
        tender = _tender(db_session, price=None, application_end=None, procurement_method=None)
        fields = bitrix_deal_service.build_deal_fields(db_session, tender)
        assert "OPPORTUNITY" not in fields
        assert "UF_CRM_1784720624836" not in fields
        assert "UF_CRM_1783503179" not in fields
        assert "UF_CRM_1784729171691" not in fields

    def test_preview_does_not_call_portal(self, db_session, portal, configured):
        tender = _tender(db_session)
        preview = bitrix_deal_service.preview_deal(db_session, tender.id)
        assert preview["action"] == "add"
        values = {item["key"]: item["value"] for item in preview["fields"]}
        assert values["CATEGORY_ID"] == "5" and values["STAGE_ID"] == "C5:PARSING"
        assert portal.calls == []


class TestPush:
    def test_refused_while_disabled(self, db_session, admin_user, portal, configured):
        tender = _tender(db_session)
        with pytest.raises(bitrix_deal_service.BitrixPushDisabledError):
            bitrix_deal_service.push_tender(db_session, tender.id, actor=admin_user)
        assert portal.calls == []

    def test_create_then_update_keeps_stage(self, db_session, admin_user, portal, configured):
        bitrix_deal_service.update_settings(db_session, {"push_enabled": True}, actor=admin_user)
        tender = _tender(db_session)

        first = bitrix_deal_service.push_tender(db_session, tender.id, actor=admin_user)
        assert first["created"] is True
        assert first["url"] == f"https://b24.example.test/crm/deal/details/{first['deal_id']}/"
        deal = portal.deals[first["deal_id"]]
        assert deal["CATEGORY_ID"] == 5 and deal["STAGE_ID"] == "C5:PARSING"

        # Менеджер сдвинул сделку; повторная отправка не возвращает её на «Парсинг».
        deal["STAGE_ID"] = "C5:SUBMITTED"
        tender.price = Decimal("999.00")
        second = bitrix_deal_service.push_tender(db_session, tender.id, actor=admin_user)
        assert second["created"] is False and second["deal_id"] == first["deal_id"]
        assert len(portal.deals) == 1
        assert portal.deals[first["deal_id"]]["STAGE_ID"] == "C5:SUBMITTED"
        assert portal.deals[first["deal_id"]]["OPPORTUNITY"] == "999.00"
        update_params = [p for m, p in portal.calls if m == "crm.deal.update"][0]
        assert "STAGE_ID" not in update_params["fields"]

        link = db_session.get(BitrixDealLink, tender.id)
        assert link.deal_id == first["deal_id"] and link.last_error is None

    def test_lost_link_finds_existing_deal(self, db_session, admin_user, portal, configured):
        bitrix_deal_service.update_settings(db_session, {"push_enabled": True}, actor=admin_user)
        tender = _tender(db_session)
        portal.deals[42] = {"ORIGIN_ID": str(tender.id), "STAGE_ID": "C5:PARSING"}

        result = bitrix_deal_service.push_tender(db_session, tender.id, actor=admin_user)
        assert result["deal_id"] == 42 and result["created"] is False
        assert "crm.deal.add" not in portal.written()


class TestCheck:
    def test_reads_only_and_reports_stage_and_missing_fields(
        self, db_session, admin_user, portal, configured
    ):
        result = bitrix_deal_service.check_connection(db_session, actor=admin_user)
        assert result["success"] is True
        assert result["stage_found"] is True
        assert "Парсинг опубликованных тендеров" in result["message"]
        assert [f["key"] for f in result["missing_fields"]] == [
            bitrix_deal_service.DEAL_FIELDS[0].key
        ]
        assert {m for m, _ in portal.calls} == {"crm.status.list", "crm.deal.fields"}

    def test_unknown_stage_is_an_error(self, db_session, admin_user, portal, configured):
        bitrix_deal_service.update_settings(db_session, {"stage_id": "C5:NOPE"}, actor=admin_user)
        result = bitrix_deal_service.check_connection(db_session, actor=admin_user)
        assert result["success"] is False
        assert len(result["stages"]) == 2

    def test_not_configured(self, db_session, admin_user):
        bitrix_deal_service.update_settings(db_session, {"webhook_url": ""}, actor=admin_user)
        result = bitrix_deal_service.check_connection(db_session, actor=admin_user)
        assert result["success"] is False and "вебхук" in result["message"]


class TestSettings:
    def test_clearing_webhook_disables_push(self, db_session, admin_user, configured):
        bitrix_deal_service.update_settings(db_session, {"push_enabled": True}, actor=admin_user)
        out = bitrix_deal_service.update_settings(db_session, {"webhook_url": ""}, actor=admin_user)
        assert out["is_configured"] is False and out["push_enabled"] is False

    def test_cannot_enable_without_webhook(self, db_session, admin_user):
        bitrix_deal_service.update_settings(db_session, {"webhook_url": ""}, actor=admin_user)
        with pytest.raises(bitrix_deal_service.BitrixNotConfiguredError):
            bitrix_deal_service.update_settings(db_session, {"push_enabled": True}, actor=admin_user)

    def test_webhook_stored_encrypted(self, db_session, admin_user, configured):
        from app.models.integration_setting import Bitrix24Settings

        stored = db_session.get(Bitrix24Settings, bitrix_deal_service._SINGLETON_ID).webhook_url
        assert "secrettoken123" not in stored


def test_api_hides_token_and_is_admin_only(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.get("/integrations/bitrix24").status_code == 401
    try:
        response = client.patch("/integrations/bitrix24", json={"webhook_url": WEBHOOK}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_configured"] is True
        assert body["push_enabled"] is False
        assert body["portal"] == "https://b24.example.test"
        assert "secrettoken123" not in response.text

        bad = client.patch(
            "/integrations/bitrix24", json={"webhook_url": "https://x.test/rest/1/a/b.json"}, headers=headers
        )
        assert bad.status_code == 400
    finally:
        client.patch("/integrations/bitrix24", json={"webhook_url": ""}, headers=headers)


class TestTestDeal:
    def test_marked_and_not_linked_even_when_push_disabled(
        self, db_session, admin_user, portal, configured
    ):
        tender = _tender(db_session)
        result = bitrix_deal_service.push_test_deal(db_session, actor=admin_user, tender_id=tender.id)

        deal = portal.deals[result["deal_id"]]
        assert deal["TITLE"].startswith("[ТЕСТ] Тендер ")
        assert deal["STAGE_ID"] == "C5:PARSING" and deal["CATEGORY_ID"] == 5
        assert deal["ORIGIN_ID"].startswith(f"test:{tender.id}:")
        assert "можно удалить" in deal["COMMENTS"]
        assert db_session.get(BitrixDealLink, tender.id) is None

        # Настоящая отправка потом не примет тестовую сделку за сделку тендера.
        bitrix_deal_service.update_settings(db_session, {"push_enabled": True}, actor=admin_user)
        real = bitrix_deal_service.push_tender(db_session, tender.id, actor=admin_user)
        assert real["created"] is True and real["deal_id"] != result["deal_id"]
