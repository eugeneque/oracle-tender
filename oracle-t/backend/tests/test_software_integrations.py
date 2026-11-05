"""Интеграции МИРТЕК в ПО верхнего уровня — данные специалиста (05.11.2026).

Проверяется то, на чём держится «Сохранить»: правки живут в черновике и не влияют на анализ
до публикации; автозаполненное без проверки не публикуется; опубликованное попадает в
карточку производителя МИРТЕК (и только его) и перекрывает публичные списки."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product
from app.models.software_integration import SoftwareIntegration
from app.services import software_integration_service as service

ACTOR = SimpleNamespace(id=None, full_name="Старший тендерный специалист")


def _seeded(db_session) -> dict[str, SoftwareIntegration]:
    service.seed_defaults(db_session)
    return {item.key: item for item in service.list_integrations(db_session)}


def _mirtek(db_session) -> Manufacturer:
    brand = f"МИРТЕК{uuid.uuid4().hex[:4].upper()}"
    manufacturer = Manufacturer(legal_name=f"ООО «{brand}»", brand_name=brand, is_mirtek=True)
    db_session.add(manufacturer)
    db_session.flush()
    return manufacturer


def _req(text: str) -> Requirement:
    return Requirement(tender_id=uuid.uuid4(), text=text, criticality="critical")


def test_seed_loads_table_and_publishes_it(db_session):
    items = _seeded(db_session)

    assert len(items) >= 20
    radio = items["radioaccess"]
    assert radio.published is not None
    assert radio.published["fields"]["protocol_spodes"]["value"] == "СПОДЭС V4"
    assert radio.published["fields"]["has_mirtek"]["verdict"] == "yes"
    assert items["teleskop"].published["fields"]["has_mirtek"]["verdict"] == "no"
    assert not service.is_dirty(radio)
    # Повторная загрузка ничего не затирает.
    assert service.seed_defaults(db_session) == 0


def test_edits_stay_in_draft_until_publish(db_session):
    item = _seeded(db_session)["enforce"]
    payload = {
        "fields": {"software_version": {"value": "С версий 2027 года", "verdict": None}},
    }
    service.update_integration(db_session, item, {"draft": payload}, actor=ACTOR)

    field = item.draft["fields"]["software_version"]
    assert field["status"] == "edited" and field["updated_by"] == ACTOR.full_name
    assert service.is_dirty(item)
    assert "2027" not in item.published["fields"]["software_version"]["value"]
    assert "2027" not in (service.integration_fact_line(item) or "")

    record = service.publish(db_session, actor=ACTOR, comment="Обновили версию")
    assert record is not None and [c["key"] for c in record.changes] == ["enforce"]
    assert "2027" in service.integration_fact_line(item)
    assert not service.is_dirty(item)
    # Нечего публиковать — записи в журнале нет.
    assert service.publish(db_session, actor=ACTOR) is None


def test_auto_filled_fields_are_not_published_until_confirmed(db_session):
    item = _seeded(db_session)["nekta"]
    draft = item.draft
    draft["fields"]["identification"] = {
        "value": "Nekta-X", "verdict": None, "status": "auto", "source": "public_list",
        "updated_at": None, "updated_by": None,
    }
    item.draft = {**draft}
    assert service.pending_review_count(item) == 1
    assert "identification" not in service.published_view(item.draft)["fields"] or (
        service.published_view(item.draft)["fields"]["identification"]["value"] != "Nekta-X"
    )

    # Пересохранение формы без правки не превращает «авто» в проверенное…
    service.update_integration(
        db_session, item, {"draft": {"fields": {"identification": {"value": "Nekta-X"}}}}, actor=ACTOR
    )
    assert item.draft["fields"]["identification"]["status"] == "auto"
    # …а явное подтверждение — превращает.
    service.update_integration(
        db_session, item,
        {"draft": {"fields": {"identification": {"value": "Nekta-X", "status": "confirmed"}}}},
        actor=ACTOR,
    )
    assert item.draft["fields"]["identification"]["status"] == "confirmed"
    assert service.is_dirty(item)


def test_unverified_functions_are_not_published(db_session):
    item = _seeded(db_session)["yaenergetik"]
    functions = [
        {"model": "МИРТЕК-32-РУ", "protocol": "СПОДЭС", "name": "Чтение журнала событий", "status": "yes", "verified": True},
        {"model": "МИРТЕК-32-РУ", "protocol": "СПОДЭС", "name": "Управление реле нагрузки", "status": "yes", "verified": False},
    ]
    service.update_integration(db_session, item, {"draft": {"functions": functions}}, actor=ACTOR)
    service.publish(db_session, actor=ACTOR)

    names = [f["name"] for f in item.published["functions"]]
    assert names == ["Чтение журнала событий"]
    assert "Чтение журнала событий" in service.integration_fact_line(item)
    assert "реле" not in service.integration_fact_line(item)


def test_mirtek_facts_named_generic_and_only_for_mirtek(db_session):
    _seeded(db_session)
    mirtek = _mirtek(db_session)
    rival = Manufacturer(legal_name="ООО «Чужой»", brand_name="Чужой", is_mirtek=False)
    db_session.add(rival)
    db_session.flush()

    named = [_req("Счётчики должны поддерживаться в ПО RadioAccess 4")]
    facts = service.mirtek_facts(db_session, mirtek, named)
    assert len(facts) == 1
    assert facts[0].startswith("[software] ДАННЫЕ СПЕЦИАЛИСТА МИРТЕК, RadioAccess 4")
    assert "протокол СПОДЭС — СПОДЭС V4" in facts[0]
    assert service.mirtek_facts(db_session, rival, named) == []
    assert service.mirtek_facts(db_session, mirtek, [_req("Класс точности 1,0")]) == []

    # Название публичной площадки узнаёт обе записи Пирамиды.
    pyramid = service.mirtek_facts(db_session, mirtek, [_req("Интеграция в ИВК «Пирамида 2.0»")])
    assert any("Пирамида 2.0" in line for line in pyramid)

    generic = service.mirtek_facts(db_session, mirtek, [_req("Приборы должны быть интегрированы в ПО верхнего уровня")])
    assert any("RadioAccess 4" in line for line in generic)
    negative = [line for line in generic if "НЕ интегрированы" in line]
    assert negative and "Телескоп" in negative[0]


def test_product_integrations_by_model(db_session):
    _seeded(db_session)
    mirtek = _mirtek(db_session)
    product = Product(
        manufacturer_id=mirtek.id, model_name="МИРТЕК-32-РУ-W3", model_code="МИРТЕК-32-РУ-W3"
    )
    other = Product(manufacturer_id=mirtek.id, model_name="МИРТЕК-12-РУ-D1", model_code="МИРТЕК-12-РУ-D1")
    db_session.add_all([product, other])
    db_session.flush()

    names32 = {row["name"] for row in service.product_integrations(db_session, product)}
    names12 = {row["name"] for row in service.product_integrations(db_session, other)}
    assert "ПО «Метроскоп»" in names32 and "ПО «Метроскоп»" not in names12
    assert "RadioAccess 4" in names12 & names32


def test_document_validation(db_session):
    item = _seeded(db_session)["enforce"]
    for bad_name, message in (("scan.emf", "PNG или PDF"), ("letter.docx", "Допустимы"), ("x.pdf", "пустой")):
        try:
            service.add_document(
                db_session, item, file_name=bad_name, content_type=None, content=b"" if "pdf" in bad_name else b"x", actor=ACTOR
            )
        except ValueError as exc:
            assert message in str(exc)
        else:  # pragma: no cover
            raise AssertionError(bad_name)
    document = service.add_document(
        db_session, item, file_name="letter.pdf", content_type="application/pdf", content=b"%PDF-1.4", actor=ACTOR
    )
    assert document.size == 8 and document.analysis_status == "none"


def test_api_overview_edit_publish(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    overview = client.get("/software-integrations", headers=headers)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert len(body["items"]) >= 20 and body["fields"][0]["key"] == "has_mirtek"

    item = next(i for i in body["items"] if i["key"] == "metroscope")
    update = client.put(
        f"/software-integrations/{item['id']}",
        headers=headers,
        json={"draft": {"fields": {"note": {"value": "Проверено на стенде Россетей"}}}},
    )
    assert update.status_code == 200, update.text
    assert update.json()["dirty"] is True

    published = client.post("/software-integrations/publish", headers=headers, json={"comment": "тест"})
    assert published.status_code == 200 and published.json()["published"] is True
    again = client.post("/software-integrations/publish", headers=headers)
    assert again.json()["published"] is False

    upload = client.post(
        f"/software-integrations/{item['id']}/documents",
        headers=headers,
        files={"file": ("scan.emf", b"x", "image/emf")},
    )
    assert upload.status_code == 400 and "PNG" in upload.json()["detail"]


def test_scan_reading_goes_to_draft_as_unverified(db_session):
    from types import SimpleNamespace as NS

    from app.services.software_integration_scan import ScanFunction, ScanReading, apply_reading

    item = _seeded(db_session)["yaenergetik"]
    doc = NS(id=uuid.uuid4(), file_name="letter.pdf")
    reading = ScanReading(
        protocol_mirtek="Миртек 32/12/135 — протокол обмена МИРТЕК",
        protocol_spodes="Миртек 32 — СПОДЭС",
        identification="",
        integration_protocols="Письмо № 38",
        software_version="",
        models=["МИРТЕК-32", "МИРТЕК-32-РУ"],
        functions=[
            ScanFunction(model="МИРТЕК-32", protocol="СПОДЭС", name="Чтение времени", status="yes", comment=""),
            ScanFunction(model="МИРТЕК-32", protocol="СПОДЭС", name="Чтение времени", status="yes", comment=""),
        ],
        summary="Письмо ООО «Технологии энергоучёта»",
    )
    before_mirtek = item.draft["fields"]["protocol_mirtek"]["value"]
    outcome = apply_reading(item, doc, reading)

    # Заполненное специалистом не перетирается — расхождение остаётся в результате разбора.
    assert item.draft["fields"]["protocol_mirtek"]["value"] == before_mirtek
    assert any(c["field"] == "protocol_spodes" for c in outcome["conflicts"])
    # Пустое поле получает предложение со статусом «авто», функции — непроверенные, без дублей.
    assert item.draft["fields"]["integration_protocols"]["status"] == "auto"
    assert outcome["functions_added"] == 1
    assert not item.draft["functions"][0]["verified"]
    assert service.pending_review_count(item) >= 2
    # Не публикуется, пока не подтверждено.
    assert "Письмо № 38" not in str(service.published_view(item.draft)["fields"].get("integration_protocols", ""))
    assert service.published_view(item.draft)["functions"] == []


def test_autofill_fills_only_empty_fields_as_auto(db_session):
    from app.adapters.upper_software import SupportedDevice
    from app.services import upper_software_service

    items = _seeded(db_session)
    mirtek = _mirtek(db_session)
    # Свежий черновик Пирамиды без данных: автозаполнению есть куда писать.
    piramida = items["piramida_20"]
    piramida.draft = service.empty_draft()
    db_session.commit()
    upper_software_service.sync_platform(
        db_session,
        "piramida",
        devices=[
            SupportedDevice(
                section="Пирамида 2.0", device_raw=f"{mirtek.brand_name}-12-РУ",
                device_names=[f"{mirtek.brand_name}-12-РУ"], manufacturer_raw=mirtek.brand_name,
            )
        ],
    )

    outcome = service.autofill_from_public_lists(db_session)

    field = piramida.draft["fields"]["has_mirtek"]
    assert outcome["filled"] >= 1
    assert field["status"] == "auto" and field["source"] == "public_list"
    # Автозаполнение не публикуется без подтверждения специалиста.
    assert "has_mirtek" not in service.published_view(piramida.draft)["fields"]
