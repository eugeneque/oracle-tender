"""Тесты подготовки к Bitrix24 (Этап 13, раздел 5.10 ТЗ): маппинг тендера на лид, выгрузка
CSV и внешний API с доступом по ключу.

Проверяется то, что ломается тихо и обнаруживается только на стороне CRM: сумма, уехавшая
в поле текстом (лид создаётся с нулём), не тот столбец в файле импорта, доступ к данным без
ключа и ключ, который остался рабочим после отзыва.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.analysis import WinPercentage
from app.models.manufacturer import Manufacturer
from app.models.region import Region, RegionResponsible
from app.models.source import Source
from app.models.tender import Tender
from app.services import api_client_service, bitrix_service
from app.services.tender_service import TenderFilters


def _source(db) -> Source:
    source = Source(
        key=f"bitrix_{uuid.uuid4().hex[:8]}",
        name="ЭТП ГПБ",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    return source


def _tender(db, **overrides) -> Tender:
    fields = {
        "title": "Поставка приборов учёта электрической энергии",
        "currency": "RUB",
        "price": Decimal("1234567.89"),
        "customer_name": 'ПАО "Россети"',
        "source_url": "https://example.test/procedure/1",
        "okpd2_code": "26.51.63.130",
        **overrides,
    }
    tender = Tender(
        source_id=_source(db).id, external_id=f"BX-{uuid.uuid4().hex[:6]}", **fields
    )
    db.add(tender)
    db.flush()
    return tender


class TestLeadMapping:
    def test_title_contains_number_and_subject(self, db_session):
        """Менеджер в списке лидов видит только название — по нему он должен понять, о какой
        закупке речь, и найти её поиском по номеру."""

        tender = _tender(db_session)
        lead = bitrix_service.build_lead(db_session, tender)

        assert tender.external_id in lead["TITLE"]
        assert "приборов учёта" in lead["TITLE"]
        assert len(lead["TITLE"]) <= 250, "Bitrix24 обрезает слишком длинные названия"

    def test_amount_is_plain_number(self, db_session):
        """Сумма должна уйти числом с точкой: «1 234 567,89» Bitrix24 при импорте примет за
        текст, и лид создастся с нулевой суммой — ошибка, заметная только в CRM."""

        tender = _tender(db_session)
        lead = bitrix_service.build_lead(db_session, tender)

        assert lead["OPPORTUNITY"] == "1234567.89"
        assert lead["CURRENCY_ID"] == "RUB"

    def test_relevance_status_maps_to_lead_stage(self, db_session):
        assert bitrix_service.build_lead(db_session, _tender(db_session))["STATUS_ID"] == "NEW"

        confirmed = _tender(db_session, relevance_status="confirmed")
        assert bitrix_service.build_lead(db_session, confirmed)["STATUS_ID"] == "IN_PROCESS"

        rejected = _tender(db_session, relevance_status="rejected")
        # Отклонённые уходят в «мусорные», а не пропадают: в CRM должно быть видно, что
        # закупку смотрели и сознательно отклонили.
        assert bitrix_service.build_lead(db_session, rejected)["STATUS_ID"] == "JUNK"

    def test_win_percentage_and_comment(self, db_session):
        tender = _tender(db_session)
        mirtek = db_session.query(Manufacturer).filter(Manufacturer.is_mirtek.is_(True)).first()
        assert mirtek is not None
        db_session.add(
            WinPercentage(
                tender_id=tender.id, manufacturer_id=mirtek.id, percentage=Decimal("87.50")
            )
        )
        db_session.flush()

        lead = bitrix_service.build_lead(db_session, tender)

        assert lead["UF_CRM_TENDER_WIN_PERCENTAGE"] == "87.5"
        assert "87.5%" in lead["COMMENTS"]
        # Оговорка про смысл цифры обязана ехать вместе с ней (раздел 5.5 ТЗ), иначе в CRM
        # её прочитают как вероятность победы.
        assert "без учёта цены" in lead["COMMENTS"]
        assert tender.source_url in lead["COMMENTS"]

    def test_responsible_comes_from_region_directory(self, db_session):
        """Ответственного в CRM неоткуда взять, кроме справочника «регион → ответственный»."""

        region = db_session.get(Region, "23") or Region(
            code="23", name="Краснодарский край", federal_district_code=3
        )
        db_session.add(region)
        db_session.flush()

        assignment = db_session.get(RegionResponsible, "23") or RegionResponsible(region_code="23")
        assignment.responsible_name = "Иванов Иван"
        db_session.add(assignment)
        db_session.flush()

        tender = _tender(db_session, region_organizer_code="23")
        lead = bitrix_service.build_lead(db_session, tender)

        assert lead["ASSIGNED_BY_NAME"] == "Иванов Иван"
        assert lead["UF_CRM_TENDER_REGION"] == "Краснодарский край"

    def test_delivery_region_falls_back_to_organizer(self, db_session):
        region = db_session.get(Region, "23") or Region(
            code="23", name="Краснодарский край", federal_district_code=3
        )
        db_session.add(region)
        db_session.flush()

        tender = _tender(db_session, region_organizer_code="23", region_delivery_code=None)
        lead = bitrix_service.build_lead(db_session, tender)

        assert lead["UF_CRM_TENDER_DELIVERY_REGION"] == "Краснодарский край"


class TestLeadsCsv:
    def test_csv_has_titles_and_field_keys(self, db_session, admin_user):
        """Две строки заголовков: русские названия для человека и технические имена полей —
        при импорте Bitrix24 просит сопоставить колонки, и без вторых это делается наугад."""

        _tender(db_session)
        content, rows = bitrix_service.build_leads_csv(
            db_session, TenderFilters(), limit=5, actor=admin_user
        )

        assert rows >= 1
        text = content.decode("utf-8")
        assert text.startswith("﻿"), "без BOM Excel открывает файл кракозябрами"

        reader = csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";")
        titles = next(reader)
        keys = next(reader)

        assert titles[0] == "Название лида"
        assert keys[0] == "TITLE"
        assert "UF_CRM_TENDER_NUMBER" in keys
        assert len(titles) == len(keys) == len(bitrix_service.LEAD_FIELDS)

    def test_csv_rows_match_field_order(self, db_session, admin_user):
        tender = _tender(db_session)
        content, _rows = bitrix_service.build_leads_csv(
            db_session, TenderFilters(search=tender.title), limit=50, actor=admin_user
        )

        reader = csv.reader(io.StringIO(content.decode("utf-8").lstrip("﻿")), delimiter=";")
        keys = next(reader)[:0] or next(reader)  # пропускаем строку названий, берём ключи
        data_rows = list(reader)

        row = next(r for r in data_rows if tender.external_id in r[keys.index("TITLE")])
        assert row[keys.index("UF_CRM_TENDER_NUMBER")] == tender.external_id
        assert row[keys.index("UF_CRM_TENDER_OKPD2")] == "26.51.63.130"


class TestApiClients:
    def test_key_is_hashed_and_returned_once(self, db_session, admin_user):
        client, key = api_client_service.create_client(
            db_session, name="Bitrix24 портал", actor=admin_user
        )

        assert key.startswith(api_client_service.KEY_PREFIX)
        assert client.key_hash != key, "ключ не должен храниться в открытом виде"
        assert key not in client.key_hash
        assert client.key_prefix == key[: api_client_service.PREFIX_LENGTH]

    def test_authenticate_accepts_valid_key_and_records_use(self, db_session, admin_user):
        client, key = api_client_service.create_client(
            db_session, name="Интеграция", actor=admin_user
        )

        authenticated = api_client_service.authenticate(db_session, key)

        assert authenticated is not None and authenticated.id == client.id
        # Отметка использования — единственный способ понять, жива ли интеграция и можно ли
        # безопасно отзывать ключ.
        assert authenticated.last_used_at is not None

    def test_authenticate_rejects_wrong_and_revoked_keys(self, db_session, admin_user):
        client, key = api_client_service.create_client(
            db_session, name="Интеграция", actor=admin_user
        )

        assert api_client_service.authenticate(db_session, None) is None
        assert api_client_service.authenticate(db_session, "просто строка") is None
        assert api_client_service.authenticate(db_session, key + "x") is None

        api_client_service.revoke_client(db_session, client.id, actor=admin_user)
        assert api_client_service.authenticate(db_session, key) is None, (
            "отозванный ключ обязан перестать работать немедленно"
        )


class TestIntegrationApi:
    @pytest.fixture()
    def api_key(self, client, admin_token) -> str:
        response = client.post(
            "/integrations/api-clients",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"test-{uuid.uuid4().hex[:6]}"},
        )
        assert response.status_code == 200, response.text
        return response.json()["key"]

    def test_requires_api_key(self, client):
        assert client.get("/integration/v1/tenders").status_code == 401
        assert (
            client.get(
                "/integration/v1/tenders", headers={"X-API-Key": "orct_invalid-key"}
            ).status_code
            == 401
        )

    def test_user_token_is_not_enough(self, client, admin_token):
        """Пользовательский JWT не должен открывать внешний API: у интеграции отдельный
        контур доступа, и смешение двух схем стирает границу между ними."""

        response = client.get(
            "/integration/v1/tenders", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 401

    def test_tenders_listing(self, client, api_key):
        response = client.get(
            "/integration/v1/tenders?limit=5", headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "items" in body and "total" in body

    def test_updated_since_filters_out_old_records(self, client, api_key):
        """Инкрементальная выборка — главный сценарий интеграции: без неё CRM будет каждый
        раз перечитывать все тысячи закупок."""

        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        response = client.get(
            "/integration/v1/tenders",
            params={"updated_since": future},
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_updated_since_survives_unencoded_plus(self, client, api_key):
        """Смещение часового пояса пишется через `+`, а в query-строке `+` означает пробел.
        Клиент, забывший про кодирование, получал бы 422 с невнятной жалобой на формат."""

        response = client.get(
            "/integration/v1/tenders?updated_since=2030-01-01T00:00:00 00:00&limit=1",
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_updated_since_rejects_garbage_with_clear_message(self, client, api_key):
        response = client.get(
            "/integration/v1/tenders",
            params={"updated_since": "позавчера"},
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 422
        assert "ISO 8601" in response.json()["detail"]

    def test_leads_endpoint_returns_field_map(self, client, api_key):
        response = client.get("/integration/v1/leads?limit=3", headers={"X-API-Key": api_key})
        assert response.status_code == 200, response.text
        body = response.json()

        keys = {field["key"] for field in body["fields"]}
        assert "TITLE" in keys and "UF_CRM_TENDER_NUMBER" in keys
        for lead in body["items"]:
            assert set(lead).issuperset(keys)

    def test_health_confirms_key_without_data(self, client, api_key):
        response = client.get("/integration/v1/health", headers={"X-API-Key": api_key})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_revoked_key_stops_working(self, client, admin_token, api_key):
        clients = client.get(
            "/integrations/api-clients", headers={"Authorization": f"Bearer {admin_token}"}
        ).json()
        target = next(c for c in clients if api_key.startswith(c["key_prefix"]))

        revoke = client.delete(
            f"/integrations/api-clients/{target['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert revoke.status_code == 200
        assert revoke.json()["is_active"] is False

        assert (
            client.get("/integration/v1/health", headers={"X-API-Key": api_key}).status_code
            == 401
        )

    def test_api_clients_are_admin_only(self, client, admin_token):
        created = client.post(
            "/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": f"bx_user_{uuid.uuid4().hex[:6]}",
                "password": "BxUser12345!",
                "full_name": "Обычный пользователь",
                "role": "user",
            },
        )
        assert created.status_code in (200, 201), created.text
        token = client.post(
            "/auth/login",
            json={"username": created.json()["username"], "password": "BxUser12345!"},
        ).json()["access_token"]

        assert (
            client.get(
                "/integrations/api-clients", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 403
        )


def test_bitrix_csv_endpoint(client, admin_token):
    response = client.get(
        "/export/bitrix24-leads.csv?limit=5", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.decode("utf-8").startswith("﻿")
