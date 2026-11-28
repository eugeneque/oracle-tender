"""Справочник приборов: разные оформления описаний СИ и проверка характеристик (27.11.2026)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text

from app.adapters.fgis import SiSearchResult
from app.models.meter_market import MeterMarketManufacturer, MeterMarketModification, MeterMarketType, MeterMarketVariant
from app.services import meter_market_characteristics as characteristics
from app.services import meter_market_dictionary as dictionary
from app.services import meter_market_interface as interface_rules
from app.services import meter_market_sync

FOBOS_RAW = "ФОБОС 1 230В 5(80)А IQOG(2)LM(1)-C"
FOBOS_DESCRIPTION = """Лист № 1
Описание средства измерений
Счетчики электрической энергии статические однофазные ФОБОС 1
Лист № 6
Структура обозначения исполнений счетчиков
S(n) - счетчик исполнения «Сплит»
М(n) - счетчик шкафного исполнения с возможностью установки сменного модуля связи
W - модификация без радиомодема
нет символа W - счетчик c радиомодемом
O - наличие оптического порта
Q - счетчик измеряет показатели качества электроэнергии
I - наличие контроля силы переменного тока в нулевом проводе
""" + "Промежуточный текст. " * 600 + """
Лист № 11
Таблица 3 – Технические характеристики
Наличие дополнительных интерфейсов связи1, 2, 3:
– модификация R – RS-485, скорость, бит/с, не менее 9600
– модификация E** – Ethernet, скорость, Мбит/с, не менее 10
– модификация Wi – Wi-Fi, скорость, Мбит/с, не менее 1
– модификация G(1) – G(5), G(6) – GSM (GPRS, 2G, 3G, 4G, 5G),
NB-IoT соответственно -
Основные поддерживаемые протоколы обмена:
– по радиоинтерфейсу NB-Fi, СПОДЭС
"""

NARTIS_DESCRIPTION = """Лист № 1
Описание средства измерений
Счетчики электрической энергии однофазные многофункциональные «НАРТИС-12-МТ»
Лист № 2
Структура условного обозначения
W1 – для установки на щиток, модификация 1
D1 – для установки на DIN-рейку, модификация 1
Основной интерфейс
RS485 – интерфейс RS-485
RF2400/n – радиоинтерфейс 2400 МГц, где n – номер модификации модуля
Дополнительные интерфейсы
G/n – радиоинтерфейс GSM/GPRS, где n – номер модификации модуля
Поддерживаемые протоколы передачи данных
P1 – протокол DLMS/COSEM
"""


def _modification(db_session, raw=FOBOS_RAW, description=FOBOS_DESCRIPTION):
    code = f"9{uuid.uuid4().int % 10000:04d}-17"
    meter_type = MeterMarketType(mit_number=code, notation="ФОБОС 1", description_type_text=description)
    db_session.add(meter_type)
    db_session.flush()
    modification = MeterMarketModification(type_id=meter_type.id, name=raw)
    db_session.add(modification)
    db_session.flush()
    return meter_type, modification


def test_relevant_description_reaches_remote_interface_table():
    excerpt = characteristics.relevant_description(FOBOS_DESCRIPTION)
    assert "М(n) - счетчик шкафного" in excerpt
    assert "модификация G(1)" in excerpt and "NB-IoT соответственно" in excerpt


def test_fobos_script_decodes_parenthesized_codes_and_radio_modem():
    codes = interface_rules.parse_table(interface_rules.codes_from_description(FOBOS_DESCRIPTION))
    assert codes.exact["G(2)"].label == "GSM" and codes.exact["G(6)"].label == "NB-IoT"
    assert codes.exact["R"].label == "RS" and codes.exact["E"].label == "Ethernet"
    assert "O" not in codes.exact and "Q" not in codes.exact and "I" not in codes.exact

    attributes = characteristics.script_attributes(FOBOS_RAW, FOBOS_DESCRIPTION)
    assert attributes["phase"].value == "1ф"
    assert attributes["design"].value == "cabinet"
    assert attributes["interface"].value == "GSM, NB-Fi"
    assert attributes["design"].evidence[0]["page"] == "6"
    assert attributes["interface"].evidence[0]["page"] == "11"
    assert "нет символа W" in attributes["interface"].reason


def test_nartis_design_and_analyst_interface_table():
    raw = "НАРТИС-12-МТ-W1-A1R1-230-5-80A-ST-RS485-G/2-P1-D"
    assert characteristics.script_attributes(raw, NARTIS_DESCRIPTION)["design"].value == "cabinet"
    din = raw.replace("-W1-", "-D1-")
    assert characteristics.script_attributes(din, NARTIS_DESCRIPTION)["design"].value == "din"
    decision = interface_rules.decode(raw, interface_rules.parse_table(interface_rules.NARTIS_INTERFACE_CODES))
    assert decision.label == "RS, GSM" and decision.review is None


def test_script_wins_over_pending_ai_and_manual_is_protected(db_session):
    meter_type, modification = _modification(db_session)
    characteristics.apply_attribute(
        db_session,
        modification,
        "interface",
        "н/д",
        source="ai",
        status="pending",
        reason="Прежнее предложение ИИ",
    )
    assert characteristics.apply_script_to_type(db_session, meter_type) == 3
    assert (modification.phase, modification.design, modification.interface) == ("1ф", "cabinet", "GSM, NB-Fi")
    metadata = characteristics.attributes_for(db_session, [modification.id])[modification.id]
    assert metadata["interface"]["source"] == "script" and metadata["interface"]["status"] == "confirmed"

    characteristics.apply_attribute(
        db_session, modification, "design", "din", source="ai", status="pending", reason="Новая догадка"
    )
    assert characteristics.confirm_attribute(db_session, modification, "design")
    characteristics.apply_attribute(
        db_session,
        modification,
        "design",
        "split",
        source="script",
        status="confirmed",
        only_if_unknown=True,
    )
    assert modification.design == "din"


def test_reject_leaves_unresolved_and_blocks_repeated_automation(db_session):
    _, modification = _modification(db_session)
    characteristics.apply_attribute(
        db_session, modification, "design", "cabinet", source="ai", status="pending", reason="M(1)"
    )
    assert characteristics.reject_attribute(db_session, modification, "design")
    assert modification.design == "nd"
    review = characteristics.attributes_for(db_session, [modification.id])[modification.id]["design"]
    assert review["source"] == "manual" and review["status"] == "unresolved"
    characteristics.apply_script_to_type(db_session, db_session.get(MeterMarketType, modification.type_id))
    assert modification.design == "nd"


def test_manual_patch_marks_only_changed_fields(db_session):
    _, modification = _modification(db_session)
    modification.phase = "1ф"
    db_session.flush()
    dictionary.update_modification(db_session, modification, {"phase": "1ф", "design": "cabinet", "interface": None})
    metadata = characteristics.attributes_for(db_session, [modification.id])[modification.id]
    assert set(metadata) == {"design"}
    assert metadata["design"]["source"] == "manual"


def test_attribute_confirmation_route(client, admin_token):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        _, modification = _modification(session)
        characteristics.apply_attribute(
            session, modification, "design", "cabinet", source="ai", status="pending", reason="M(1)"
        )
        session.commit()
        modification_id = modification.id
        type_id = modification.type_id
    response = client.post(
        f"/meter-market/modifications/{modification_id}/attributes/design/confirm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["attributes"]["design"]["source"] == "manual"
    repeated = client.post(
        f"/meter-market/modifications/{modification_id}/attributes/design/confirm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert repeated.status_code == 409
    with SessionLocal() as session:
        modification = session.get(MeterMarketModification, modification_id)
        if modification is not None:
            session.delete(modification)
        meter_type = session.get(MeterMarketType, type_id)
        if meter_type is not None:
            session.delete(meter_type)
        session.commit()


class _DescriptionAdapter:
    def fetch_description_type_text(self, url, *, mirror_url=None):
        assert url == "https://example.test/description.pdf"
        return FOBOS_DESCRIPTION


def test_refresh_description_uses_meter_type_document(db_session):
    meter_type, _ = _modification(db_session)
    meter_type.description_type_url = "https://example.test/description.pdf"
    meter_type.description_type_text = None
    extracted = characteristics.refresh_description(db_session, meter_type, adapter=_DescriptionAdapter())
    assert extracted == FOBOS_DESCRIPTION
    assert meter_type.description_type_text == FOBOS_DESCRIPTION
    assert meter_type.description_type_fetched_at is not None


class _FakeAdapter:
    def __init__(self, results):
        self.results = results

    def search_by_manufacturer(self, legal_name, *, fetch_cards=True):
        return self.results


def test_sync_adds_registry_devices_without_including_them_in_report(db_session):
    manufacturer = MeterMarketManufacturer(name="Телематические Решения")
    db_session.add(manufacturer)
    db_session.flush()
    code = f"8{uuid.uuid4().int % 10000:04d}-25"
    result = SiSearchResult(
        si_code=code,
        type_name="Счетчики электрической энергии статические однофазные ФОБОС 1",
        notation="ФОБОС 1",
        tested_modifications=[FOBOS_RAW],
        description_type_url="https://example.test/description.pdf",
        description_type_version="1",
    )
    stats = meter_market_sync.sync_manufacturer(db_session, manufacturer, adapter=_FakeAdapter([result]))
    assert (stats.found, stats.types_created, stats.modifications_created) == (1, 1, 1)
    meter_type = db_session.scalar(select(MeterMarketType).where(MeterMarketType.mit_number == code))
    assert meter_type.in_report is False and meter_type.review_status == "new"
    assert meter_type.description_type_url.endswith("description.pdf")
    assert db_session.scalar(select(MeterMarketModification).where(MeterMarketModification.type_id == meter_type.id)).name == FOBOS_RAW
    again = meter_market_sync.sync_manufacturer(db_session, manufacturer, adapter=_FakeAdapter([result]))
    assert again.types_created == 0 and again.modifications_created == 0



def _delete_type(session, type_id):
    meter_type = session.get(MeterMarketType, type_id)
    if meter_type is not None:
        session.delete(meter_type)
        session.commit()


def test_devices_route_returns_flat_rows_and_review_filter(client, admin_token):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        meter_type, modification = _modification(session)
        characteristics.apply_attribute(
            session, modification, "design", "cabinet", source="ai", status="pending", reason="M(1)"
        )
        session.commit()
        type_id = meter_type.id
        modification_id = modification.id
        mit_number = meter_type.mit_number
    try:
        response = client.get("/meter-market/devices?review=pending", headers={"Authorization": f"Bearer {admin_token}"})
        assert response.status_code == 200, response.text
        row = next(item for item in response.json() if item["id"] == modification_id)
        assert row["mit_number"] == mit_number
        assert row["attributes"]["design"]["status"] == "pending"
        excluded = client.get("/meter-market/devices?review=unresolved", headers={"Authorization": f"Bearer {admin_token}"})
        assert modification_id not in [item["id"] for item in excluded.json()]
    finally:
        with SessionLocal() as session:
            _delete_type(session, type_id)


def test_selected_types_markup_route_processes_only_requested_ids(client, admin_token, monkeypatch):
    from app.db.session import SessionLocal
    from app.services import meter_market_markup

    with SessionLocal() as session:
        meter_type, _ = _modification(session)
        session.commit()
        type_id = meter_type.id
        mit_number = meter_type.mit_number
    called = []

    def fake_markup(session, meter_type, **kwargs):
        called.append(meter_type.mit_number)
        return {"script": 2, "mapped": 0, "created": 0, "failed": 0}

    monkeypatch.setattr(meter_market_markup, "markup_type", fake_markup)
    try:
        response = client.post(
            "/meter-market/types/markup",
            json={"type_ids": [type_id]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200, response.text
        key = response.json()["job"]
        for _ in range(50):
            jobs = client.get("/meter-market/catalog-jobs", headers={"Authorization": f"Bearer {admin_token}"}).json()
            job = next(item for item in jobs if item["key"] == key)
            if job["state"] != "running":
                break
        assert job["state"] == "done", job
        assert job["result"]["script"] == 2
        assert called == [mit_number]
    finally:
        with SessionLocal() as session:
            _delete_type(session, type_id)


def test_output_limit_stops_markup_after_two_failed_chunks(db_session, monkeypatch):
    from app.services import meter_market_markup

    meter_type, _ = _modification(db_session)
    for index in range(18):
        variant = MeterMarketVariant(type_id=meter_type.id, raw=f"Написание {index}", norm_key=f"key-{index}")
        db_session.add(variant)
        db_session.flush()
        db_session.execute(
            text("INSERT INTO meter_verification_monthly (variant_id, month, count) VALUES (:v, '2026-07-01', 10)"),
            {"v": variant.id},
        )
    calls = []

    def fail(db, **kwargs):
        calls.append(1)
        raise ValueError("ответ оборван по лимиту токенов")

    monkeypatch.setattr(meter_market_markup, "run_structured", fail)
    stats = meter_market_markup.markup_type(db_session, meter_type, recheck=True)
    assert stats["stopped"] == 1 and stats["failed"] == 2
    assert len(calls) == 2


def test_batch_markup_error_contains_partial_result_and_progress(client, admin_token, monkeypatch):
    from app.db.session import SessionLocal
    from app.services import meter_market_markup

    with SessionLocal() as session:
        first, _ = _modification(session)
        second, _ = _modification(session)
        session.commit()
        ids = [first.id, second.id]
    called = []

    def fake_markup(session, meter_type, **kwargs):
        called.append(meter_type.mit_number)
        return {"script": 3, "mapped": 0, "created": 0, "failed": 2, "stopped": 1}

    monkeypatch.setattr(meter_market_markup, "markup_type", fake_markup)
    try:
        response = client.post(
            "/meter-market/types/markup",
            json={"type_ids": ids},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200, response.text
        key = response.json()["job"]
        for _ in range(50):
            jobs = client.get("/meter-market/catalog-jobs", headers={"Authorization": f"Bearer {admin_token}"}).json()
            job = next(item for item in jobs if item["key"] == key)
            if job["state"] != "running":
                break
        assert job["state"] == "error", job
        assert job["result"]["types"] == 1 and job["result"]["script"] == 3
        assert job["progress"]["current"] == 1 and job["progress"]["total"] == 2
        assert len(called) == 1
    finally:
        with SessionLocal() as session:
            for type_id in ids:
                _delete_type(session, type_id)


def test_full_markup_only_selects_types_with_missing_attributes(client, admin_token, monkeypatch):
    from app.db.session import SessionLocal
    from app.services import meter_market_markup

    with SessionLocal() as session:
        complete_type, complete = _modification(session)
        complete.phase = "1ф"
        complete.design = "cabinet"
        complete.interface = "RS"
        missing_type, missing = _modification(session)
        missing.design = "nd"
        session.commit()
        ids = [complete_type.id, missing_type.id]
        missing_mit = missing_type.mit_number
    called = []

    def fake_markup(session, meter_type, **kwargs):
        called.append(meter_type.mit_number)
        return {"script": 0, "mapped": 0, "created": 0, "failed": 0}

    monkeypatch.setattr(meter_market_markup, "markup_type", fake_markup)
    try:
        response = client.post(
            "/meter-market/types/markup",
            json={"type_ids": None},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200, response.text
        key = response.json()["job"]
        for _ in range(50):
            jobs = client.get("/meter-market/catalog-jobs", headers={"Authorization": f"Bearer {admin_token}"}).json()
            job = next(item for item in jobs if item["key"] == key)
            if job["state"] != "running":
                break
        assert job["state"] == "done", job
        assert called == [missing_mit]
    finally:
        with SessionLocal() as session:
            for type_id in ids:
                _delete_type(session, type_id)


def test_cancel_batch_markup_stops_with_partial_result(client, admin_token, monkeypatch):
    import time

    from app.db.session import SessionLocal
    from app.services import meter_market_markup

    with SessionLocal() as session:
        first, _ = _modification(session)
        second, _ = _modification(session)
        session.commit()
        ids = [first.id, second.id]
    called = []

    def fake_markup(session, meter_type, **kwargs):
        called.append(meter_type.mit_number)
        time.sleep(0.05)
        return {"script": 1, "mapped": 0, "created": 0, "failed": 0}

    monkeypatch.setattr(meter_market_markup, "markup_type", fake_markup)
    try:
        response = client.post(
            "/meter-market/types/markup",
            json={"type_ids": ids},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        key = response.json()["job"]
        cancelled = client.post(f"/meter-market/catalog-jobs/{key}/cancel", headers={"Authorization": f"Bearer {admin_token}"})
        assert cancelled.status_code == 200, cancelled.text
        for _ in range(50):
            jobs = client.get("/meter-market/catalog-jobs", headers={"Authorization": f"Bearer {admin_token}"}).json()
            job = next(item for item in jobs if item["key"] == key)
            if job["state"] != "running":
                break
        assert job["state"] == "error" and job["error"] == "Остановлено пользователем"
        assert job["result"]["types"] <= 1
        assert len(called) <= 1
    finally:
        with SessionLocal() as session:
            for type_id in ids:
                _delete_type(session, type_id)


def _variant_with_count(db_session, meter_type, modification=None, raw=FOBOS_RAW):
    variant = MeterMarketVariant(
        type_id=meter_type.id,
        raw=raw,
        norm_key=f"variant-{uuid.uuid4().hex}",
        modification_id=modification.id if modification else None,
    )
    db_session.add(variant)
    db_session.flush()
    db_session.execute(
        text("INSERT INTO meter_verification_monthly (variant_id, month, count) VALUES (:v, '2026-07-01', 10)"),
        {"v": variant.id},
    )
    return variant


def test_cell_mode_requests_only_missing_fields_and_preserves_existing_values(db_session, monkeypatch):
    from app.services import meter_market_markup

    meter_type, modification = _modification(db_session)
    meter_type.description_type_text = "Текст без структуры обозначения"
    modification.phase = "1ф"
    modification.interface = "RS"
    modification.mpi_years = 16
    modification.smart_890 = True
    modification.design = "nd"
    variant = _variant_with_count(db_session, meter_type, modification)
    seen = {}

    def fake(db, *, system_prompt, user_text, response_model, temperature):
        seen["system"] = system_prompt
        seen["text"] = user_text
        return response_model(
            items=[{
                "id": variant.id,
                "field": "design",
                "value": "cabinet",
                "confidence": "high",
                "reason": "M(1) — шкафное исполнение",
                "evidence": [
                    {"code": "M(1)", "page": "6", "quote": "шкафное исполнение"},
                    {"code": "M(1)", "page": "6", "quote": "вторая цитата"},
                    {"code": "M(1)", "page": "6", "quote": "X" * 500},
                ],
            }]
        )

    monkeypatch.setattr(meter_market_markup, "run_structured", fake)
    stats = meter_market_markup.markup_type(db_session, meter_type, recheck=True, cell_mode=True)
    assert stats["corrected"] == 1
    assert (modification.phase, modification.interface, modification.design) == ("1ф", "RS", "cabinet")
    assert seen["system"] == meter_market_markup.CELL_SYSTEM_PROMPT
    requested = seen["text"].split("Заполни ТОЛЬКО эти ячейки: ", 1)[1].splitlines()[0]
    assert requested == "design"
    metadata = characteristics.attributes_for(db_session, [modification.id])[modification.id]
    assert metadata["design"]["status"] == "pending" and metadata["design"]["evidence"][0]["page"] == "6"
    assert len(metadata["design"]["evidence"]) == 2
    assert all(len(item["quote"]) <= 300 for item in metadata["design"]["evidence"])


def test_cell_mode_first_maps_spelling_then_fills_only_requested_cells(db_session, monkeypatch):
    from app.services import meter_market_markup

    meter_type, _ = _modification(db_session)
    meter_type.description_type_text = "Текст без структуры обозначения и без фазности"
    variant = _variant_with_count(db_session, meter_type)
    calls = []

    def fake(db, *, system_prompt, user_text, response_model, temperature):
        calls.append(response_model.__name__)
        if response_model.__name__ == "MappingAnswer":
            return response_model(items=[{"id": variant.id, "name": "ФОБОС 1 IQOG(2)LM(1)-C", "confidence": "high", "reason": "Коды совпали"}])
        return response_model(items=[{
            "id": variant.id,
            "field": "design",
            "value": "cabinet",
            "confidence": "high",
            "reason": "M(1)",
            "evidence": [],
        }])

    monkeypatch.setattr(meter_market_markup, "run_structured", fake)
    stats = meter_market_markup.markup_type(db_session, meter_type, recheck=True, cell_mode=True)
    modification = db_session.scalar(
        select(MeterMarketModification).where(
            MeterMarketModification.type_id == meter_type.id,
            MeterMarketModification.name == "ФОБОС 1 IQOG(2)LM(1)-C",
        )
    )
    assert stats["created"] == 1 and stats["mapped"] == 1 and stats["corrected"] == 1
    assert calls == ["MappingAnswer", "CellAnswer", "CellAnswer"]
    assert modification.design == "cabinet"
    assert modification.phase is None and modification.interface is None and modification.smart_890 is None
    assert modification.mpi_years is None


def test_cell_mode_splits_missing_cells_into_small_batches(db_session, monkeypatch):
    from app.services import meter_market_markup

    meter_type, modification = _modification(db_session)
    meter_type.description_type_text = "Текст без структуры обозначения"
    modification.design = "nd"
    modification.interface = None
    modification.mpi_years = None
    modification.smart_890 = None
    _variant_with_count(db_session, meter_type, modification)
    _variant_with_count(db_session, meter_type, modification, raw=FOBOS_RAW + " A")
    calls = []

    def fake(db, *, system_prompt, user_text, response_model, temperature):
        cells = user_text.split("Ячейки (id; field; написание; модификация):", 1)[1]
        calls.append((response_model.__name__, cells.count("- id=")))
        return response_model(items=[])

    monkeypatch.setattr(meter_market_markup, "run_structured", fake)
    meter_market_markup.markup_type(db_session, meter_type, recheck=True, cell_mode=True)
    assert calls == [("CellAnswer", 4), ("CellAnswer", 4), ("CellAnswer", 2)]
