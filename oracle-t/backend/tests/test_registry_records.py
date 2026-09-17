"""Реестры допуска: ПП 719 (ГИСП), ЗАК ПАО «Россети», реестр российского ПО
(замечание тестировщика 16.09.2026).

Проверяются три вещи, которые нельзя доверить модели: узнавание реестра в формулировке
ТЗ (пишут по-разному — «ПП 719», «реестр Минпромторга», «аттестационная комиссия»),
состояние записи по датам («действующее ЗАК» — это арифметика, а не мнение) и то, что в
карточку производителя для сопоставления попадают только факты, а не «мы не заводили».
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product
from app.models.registry_record import ProductRegistryRecord
from app.models.source import Source
from app.models.tender import Tender
from app.services import registry_records_service as service
from app.services.compliance_service import build_manufacturer_context


class TestDetection:
    def test_industrial_products_wordings(self):
        for text in (
            "Товар должен быть включён в реестр российской промышленной продукции",
            "Наличие записи в реестре промышленной продукции (ПП РФ № 719)",
            "Подтверждение производства на территории РФ согласно постановлению Правительства № 719",
            "Выписка из ГИСП",
            "Продукция включена в реестр Минпромторга",
        ):
            assert service.detect_registries(text) == ["industrial_products"], text

    def test_rosseti_attestation_wordings(self):
        for text in (
            "Наличие действующего ЗАК ПАО «Россети»",
            "Оборудование должно иметь заключение аттестационной комиссии",
            "К установке допускается оборудование, включённое в Перечень оборудования, материалов и систем, допущенных ПАО «Россети» к применению",
            "Приборы учёта аттестованы в ПАО «Россети»",
        ):
            assert service.detect_registries(text) == ["rosseti_attestation"], text

    def test_software_registry_and_negatives(self):
        assert service.detect_registries(
            "ПО должно быть внесено в Единый реестр российских программ для ЭВМ и баз данных"
        ) == ["software_registry"]
        # Госреестр СИ — не реестр допуска, им занимается описание типа; закупка № 7190…
        # и слово «ЗАКУПКА» не должны цепляться.
        for text in (
            "Товар должен быть включён в Государственный реестр средств измерений",
            "Закупка № 71900012345 ЗАКУПКА",
            "Требования к участнику закупки",
            None,
        ):
            assert service.detect_registries(text) == [], text


class TestRecordState:
    def _record(self, **kwargs) -> ProductRegistryRecord:
        return ProductRegistryRecord(
            product_id=uuid.uuid4(), registry="rosseti_attestation", presence="present", **kwargs
        )

    def test_states_by_dates(self):
        today = date(2026, 9, 17)
        assert service.record_state(None, today) == "unknown"
        assert service.record_state(self._record(valid_to=None), today) == "active"
        assert service.record_state(self._record(valid_to=date(2028, 1, 1)), today) == "active"
        assert service.record_state(self._record(valid_to=today + timedelta(days=30)), today) == "expiring"
        assert service.record_state(self._record(valid_to=today - timedelta(days=1)), today) == "expired"
        absent = self._record(valid_to=date(2030, 1, 1))
        absent.presence = "absent"
        assert service.record_state(absent, today) == "absent"

    def test_describe_record(self):
        today = date(2026, 9, 17)
        record = self._record(record_number="ЗАК-123", issued_at=date(2024, 3, 1), valid_to=date(2029, 3, 1))
        assert service.describe_record(record, today) == "№ ЗАК-123 от 01.03.2024 до 01.03.2029 (действует)"


def _make_manufacturer(db_session, *, is_mirtek=False) -> Manufacturer:
    brand = f"ЗАВОД{uuid.uuid4().hex[:6].upper()}"
    manufacturer = Manufacturer(legal_name=f"ООО «{brand}»", brand_name=brand, is_mirtek=is_mirtek)
    db_session.add(manufacturer)
    db_session.flush()
    return manufacturer


def _make_product(db_session, manufacturer, tail: str) -> Product:
    product = Product(manufacturer_id=manufacturer.id, model_name=f"{manufacturer.brand_name}-{tail}")
    db_session.add(product)
    db_session.flush()
    return product


def _make_tender(db_session, requirement_texts: list[str]) -> tuple[Tender, list[Requirement]]:
    suffix = uuid.uuid4().hex[:8]
    source = Source(key=f"reg_t_{suffix}", name="Источник", url=f"https://example.test/{suffix}", type="eis")
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"REG-{suffix}", title="Поставка счётчиков")
    db_session.add(tender)
    db_session.flush()
    requirements = [
        Requirement(tender_id=tender.id, text=text, criticality="critical") for text in requirement_texts
    ]
    db_session.add_all(requirements)
    db_session.commit()
    return tender, requirements


def test_upsert_validation_and_absent_clears_number(db_session):
    manufacturer = _make_manufacturer(db_session)
    product = _make_product(db_session, manufacturer, "32")
    db_session.commit()

    with pytest.raises(ValueError):
        service.upsert_record(
            db_session, product, "industrial_products", presence="present", record_number="",
            issued_at=None, valid_to=None, url=None, note=None, verified=False, actor=None,
        )
    with pytest.raises(ValueError):
        service.upsert_record(
            db_session, product, "industrial_products", presence="present", record_number="1",
            issued_at=date(2026, 1, 1), valid_to=date(2025, 1, 1), url=None, note=None,
            verified=False, actor=None,
        )

    record = service.upsert_record(
        db_session, product, "industrial_products", presence="absent", record_number="1234",
        issued_at=date(2026, 1, 1), valid_to=date(2027, 1, 1), url=None, note="сверено 17.09",
        verified=True, actor=None,
    )
    assert record.record_number is None and record.valid_to is None
    assert service.record_state(record) == "absent"
    # Повторный вызов обновляет ту же запись, а не плодит вторую.
    again = service.upsert_record(
        db_session, product, "industrial_products", presence="present", record_number="5678",
        issued_at=None, valid_to=None, url="https://gisp.gov.ru/x", note=None, verified=False, actor=None,
    )
    assert again.id == record.id and again.record_number == "5678"
    assert len(service.list_records(db_session, product.id)) == 1


def test_registry_facts_and_tender_overview(db_session):
    """В карточку попадают факты только по спрошенным реестрам; незаведённая запись —
    не факт. Сводка по закупке отдаёт лучшее состояние по производителю."""

    mirtek = _make_manufacturer(db_session, is_mirtek=True)
    active = _make_product(db_session, mirtek, "32-РУ")
    expired = _make_product(db_session, mirtek, "1")
    other = _make_manufacturer(db_session)
    _make_product(db_session, other, "X")
    db_session.commit()

    service.upsert_record(
        db_session, active, "rosseti_attestation", presence="present", record_number="ЗАК-1",
        issued_at=date(2024, 1, 1), valid_to=date.today() + timedelta(days=400), url=None,
        note=None, verified=True, actor=None,
    )
    service.upsert_record(
        db_session, expired, "rosseti_attestation", presence="present", record_number="ЗАК-0",
        issued_at=date(2019, 1, 1), valid_to=date(2024, 1, 1), url=None, note=None,
        verified=False, actor=None,
    )
    # Запись по реестру, о котором закупка не спрашивает, — в карточку не попадает.
    service.upsert_record(
        db_session, active, "software_registry", presence="present", record_number="ПО-7",
        issued_at=None, valid_to=None, url=None, note=None, verified=False, actor=None,
    )

    tender, requirements = _make_tender(
        db_session,
        ["Наличие действующего ЗАК ПАО «Россети»", "Класс точности 1,0"],
    )

    facts = service.registry_facts(db_session, mirtek, requirements)
    assert len(facts) == 2
    assert any(f"[registry] {active.model_name}: ЗАК ПАО «Россети» — № ЗАК-1" in f and "(действует)" in f for f in facts)
    assert any(expired.model_name in f and "(истекла)" in f for f in facts)
    assert not any("ПО-7" in f for f in facts)
    assert service.registry_facts(db_session, other, requirements) == []

    context, _ = build_manufacturer_context(
        db_session, mirtek, include_manual=False, requirements=requirements
    )
    assert "[registry]" in context

    overview = service.tender_overview(db_session, tender)
    assert overview["mentioned_registries"] == ["rosseti_attestation"]
    assert overview["requirements"][0]["registries"] == ["rosseti_attestation"]
    assert next(r for r in overview["registries"] if r["key"] == "rosseti_attestation")["mentioned"]
    row = next(m for m in overview["manufacturers"] if m["manufacturer_id"] == mirtek.id)
    zak = row["registries"]["rosseti_attestation"]
    assert zak["state"] == "active"
    assert [p["model_name"] for p in zak["products"]] == [active.model_name, expired.model_name]
    assert row["registries"]["industrial_products"]["state"] == "unknown"
    other_row = next(m for m in overview["manufacturers"] if m["manufacturer_id"] == other.id)
    assert other_row["registries"]["rosseti_attestation"]["state"] == "unknown"


def test_registry_api_roundtrip(client, admin_token):
    # `db_session` откатывается после теста и HTTP-обработчикам не виден: модель заводится
    # в обычной сессии и убирается в конце руками.
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        manufacturer = _make_manufacturer(db)
        product = _make_product(db, manufacturer, "API")
        db.commit()
        product_id, manufacturer_id = product.id, manufacturer.id
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {admin_token}"}
    try:
        _api_roundtrip(client, headers, product_id)
    finally:
        db = SessionLocal()
        try:
            db.delete(db.get(Product, product_id))
            db.flush()
            db.delete(db.get(Manufacturer, manufacturer_id))
            db.commit()
        finally:
            db.close()


def _api_roundtrip(client, headers, product_id):
    class _P:
        id = product_id

    product = _P()

    response = client.get("/catalog/registries", headers=headers)
    assert response.status_code == 200
    assert {row["key"] for row in response.json()} == {
        "industrial_products", "rosseti_attestation", "software_registry"
    }
    gisp = next(row for row in response.json() if row["key"] == "industrial_products")
    assert gisp["url"] and "gisp.gov.ru" in gisp["url"]

    response = client.put(
        f"/products/{product.id}/registry-records/industrial_products",
        headers=headers,
        json={"presence": "present", "record_number": "1234/5/2026", "valid_to": "2030-01-01", "verified": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"

    response = client.get(f"/products/{product.id}/registry-records", headers=headers)
    rows = {row["registry"]: row for row in response.json()}
    assert rows["industrial_products"]["record"]["record_number"] == "1234/5/2026"
    assert rows["rosseti_attestation"]["record"] is None
    assert rows["rosseti_attestation"]["state"] == "unknown"

    response = client.put(
        f"/products/{product.id}/registry-records/nope", headers=headers, json={"presence": "absent"}
    )
    assert response.status_code == 404

    response = client.delete(f"/products/{product.id}/registry-records/industrial_products", headers=headers)
    assert response.status_code == 204
    response = client.get(f"/products/{product.id}/registry-records", headers=headers)
    assert all(row["record"] is None for row in response.json())
