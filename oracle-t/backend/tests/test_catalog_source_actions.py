"""Кнопки панели «Источники данных» карточки модели (13.10.2026) — без настоящей модели ИИ
и без обращения к сайтам: всё внешнее подменено. Проверяется, что читатель видит состояние
источников, но не может запускать действия; что загруженное руководство читается и
раздаётся семейству; что загруженная выгрузка реестра находит модель, а сбой скачивания
объясняется человеку."""

from __future__ import annotations

import io
import time
import uuid

from app.adapters.admission_registries import RegistryUnavailable
from app.db.session import SessionLocal
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ProductDataSource,
)
from app.models.registry_record import AdmissionRegistryPoll, ProductRegistryRecord
from app.seed.characteristics_data import resolve_field
from app.services import characteristic_extraction, document_discovery, product_manual_ingest, registry_polling

GISP = "industrial_products"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _reader_token(client, admin_token) -> str:
    username = f"reader_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "Т", "role": "user"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return client.post("/auth/login", json={"username": username, "password": "SomePass123"}).json()["access_token"]


def _catalog(*, models: list[str], manual_url: str | None = None) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Производитель с моделями; `manual_url` — общая ссылка на руководство семейства."""

    tag = uuid.uuid4().hex[:6].upper()
    with SessionLocal() as db:
        manufacturer = Manufacturer(legal_name=f"ООО «Проверка{tag}»", brand_name=f"Проверка{tag}", is_mirtek=False)
        db.add(manufacturer)
        db.flush()
        ids = []
        for code in models:
            product = Product(
                manufacturer_id=manufacturer.id,
                model_name=f"Счётчик {code}",
                model_code=code,
                data_source=ProductDataSource.MANUFACTURER_SITE.value,
            )
            db.add(product)
            db.flush()
            if manual_url:
                db.add(
                    ProductCharacteristic(
                        product_id=product.id, group_name="Документация", field_name="Ссылка на руководство",
                        value=manual_url, source=CharacteristicSource.MANUFACTURER_SITE.value,
                    )
                )
            ids.append(product.id)
        db.commit()
        return manufacturer.id, ids


def _sources(client, token, product_id) -> dict[str, dict]:
    resp = client.get(f"/products/{product_id}/data-sources", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return {source["key"]: source for source in resp.json()}


def _gisp_xlsx(designation: str, organization: str) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Номер реестровой записи", "Наименование продукции", "ОКПД2", "Наименование организации", "Дата внесения в реестр"])
    sheet.append(["555\\1\\2026", f"Счётчик электрической энергии {designation}", "26.51.63.130", organization, "01.03.2026"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- читатель


def test_reader_sees_sources_but_cannot_run_actions(client, admin_token, monkeypatch):
    # Состояние руководства спрашивает robots.txt сайта — в тесте сайт не опрашивается.
    monkeypatch.setattr(product_manual_ingest, "robots_allows", lambda url: True)
    _, (product_id,) = _catalog(models=["ПРВ-101"], manual_url="https://example.invalid/manual.pdf")
    reader = _reader_token(client, admin_token)

    sources = _sources(client, reader, product_id)
    assert {"site", "fgis", "manual", "gisp", "zak"} <= sources.keys()
    # Действия сервер отдаёт всем — прятать кнопки от читателя должен интерфейс; запуск
    # закрыт на сервере.
    assert any(action["ai"] for action in sources["manual"]["actions"])

    forbidden = [
        ("post", f"/products/{product_id}/read-manual", {}),
        ("post", f"/products/{product_id}/find-documentation", {}),
        ("post", f"/products/{product_id}/manual-upload", {"files": {"file": ("m.txt", b"x", "text/plain")}}),
        ("post", f"/catalog/registries/{GISP}/poll", {}),
        ("post", f"/catalog/registries/{GISP}/upload", {"files": {"file": ("g.xlsx", b"x")}}),
        ("get", "/catalog/repair-characteristics", {}),
        ("post", "/catalog/repair-characteristics", {}),
    ]
    for method, url, extra in forbidden:
        resp = getattr(client, method)(url, headers=_auth(reader), **extra)
        assert resp.status_code == 403, f"{url}: {resp.status_code}"


# --------------------------------------------------------------------------- руководство


def test_uploaded_manual_is_read_and_shared_with_family(client, admin_token, monkeypatch):
    group, field = resolve_field("", "Степень защиты корпуса")
    calls = []

    def fake_read(db, *, text, document_source):
        calls.append(text)
        reading = characteristic_extraction.DocumentReading()
        reading.chunks_processed = 1
        reading.characteristics.append(
            characteristic_extraction.ExtractedCharacteristic(group_name=group, field_name=field, value="IP54", confidence=0.9)
        )
        return reading

    monkeypatch.setattr(characteristic_extraction, "read_characteristics", fake_read)
    monkeypatch.setattr(product_manual_ingest, "robots_allows", lambda url: False)
    _, (first, sibling) = _catalog(models=["ПРВ-201", "ПРВ-202"], manual_url="https://example.invalid/family.pdf")

    # Сайт закрыт для робота — карточка просит файл, а не «прочитать».
    manual = _sources(client, admin_token, first)["manual"]
    assert manual["state"] == "blocked"
    assert [action["key"] for action in manual["actions"]] == ["upload_manual"]

    resp = client.post(
        f"/products/{first}/manual-upload",
        files={"file": ("руководство.txt", "Степень защиты IP54".encode(), "text/plain")},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1  # одно чтение на семейство
    assert resp.json()["reused_documents"] == 1

    with SessionLocal() as db:
        for product_id in (first, sibling):
            row = db.query(ProductCharacteristic).filter_by(product_id=product_id, field_name=field).one()
            assert (row.value, row.source, row.ai_extracted) == ("IP54", CharacteristicSource.USER_MANUAL.value, True)
    assert _sources(client, admin_token, first)["manual"]["state"] == "ok"


def test_uploaded_manual_reports_when_ai_did_not_answer(client, admin_token, monkeypatch):
    """Без ответа модели «сохранено 0» выглядело бы как «в руководстве ничего нет»."""

    def failed_read(db, *, text, document_source):
        reading = characteristic_extraction.DocumentReading()
        reading.chunks_failed = 1
        return reading

    monkeypatch.setattr(characteristic_extraction, "read_characteristics", failed_read)
    _, (product_id,) = _catalog(models=["ПРВ-301"])
    resp = client.post(
        f"/products/{product_id}/manual-upload",
        files={"file": ("руководство.txt", "текст".encode(), "text/plain")},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    assert "ИИ" in resp.json()["detail"]


def test_read_manual_reports_when_ai_did_not_answer(client, admin_token, monkeypatch):
    def failed_read(db, *, text, document_source):
        reading = characteristic_extraction.DocumentReading()
        reading.chunks_failed = 2
        return reading

    monkeypatch.setattr(characteristic_extraction, "read_characteristics", failed_read)
    monkeypatch.setattr(product_manual_ingest.RobotsGate, "allows", lambda self, url: True)
    monkeypatch.setattr(product_manual_ingest, "_download_text", lambda client, url: "текст руководства")
    _, (product_id,) = _catalog(models=["ПРВ-351"], manual_url="https://example.invalid/open.pdf")
    resp = client.post(f"/products/{product_id}/read-manual", headers=_auth(admin_token))
    assert resp.status_code == 422
    assert "ИИ не ответила" in resp.json()["detail"]


def test_read_manual_blocked_by_robots_explains(client, admin_token, monkeypatch):
    monkeypatch.setattr(product_manual_ingest.RobotsGate, "allows", lambda self, url: False)
    _, (product_id,) = _catalog(models=["ПРВ-401"], manual_url="https://example.invalid/closed.pdf")
    resp = client.post(f"/products/{product_id}/read-manual", headers=_auth(admin_token))
    assert resp.status_code == 422
    assert "robots.txt" in resp.json()["detail"]


def test_find_documentation_without_result_says_so(client, admin_token, monkeypatch):
    monkeypatch.setattr(document_discovery, "find_manual_for_product", lambda *args, **kwargs: None)
    _, (product_id,) = _catalog(models=["ПРВ-501"])
    resp = client.post(f"/products/{product_id}/find-documentation", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert "не найдена" in resp.json()["message"]


# --------------------------------------------------------------------------- реестры


def test_registry_upload_finds_model_and_marks_it_automatic(client, admin_token):
    designation = f"ПРВ-{uuid.uuid4().hex[:4].upper()}9"
    _, (product_id,) = _catalog(models=[designation])
    with SessionLocal() as db:
        brand = db.get(Manufacturer, db.get(Product, product_id).manufacturer_id).brand_name

    resp = client.post(
        f"/catalog/registries/{GISP}/upload",
        files={"file": ("gisp.xlsx", _gisp_xlsx(designation, f"ООО «{brand}»"))},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"

    rows = {row["registry"]: row for row in client.get(f"/products/{product_id}/registry-records", headers=_auth(admin_token)).json()}
    record = rows[GISP]["record"]
    assert record["record_number"] == "555\\1\\2026"
    assert record["auto_found_at"] is not None
    gisp = _sources(client, admin_token, product_id)["gisp"]
    assert gisp["state"] == "ok" and "найдено автоматически" in gisp["detail"]


def test_registry_upload_ignores_same_designation_from_other_maker(client, admin_token):
    designation = f"ПРВ-{uuid.uuid4().hex[:4].upper()}8"
    _, (product_id,) = _catalog(models=[designation])
    resp = client.post(
        f"/catalog/registries/{GISP}/upload",
        files={"file": ("gisp.xlsx", _gisp_xlsx(designation, "АО «Совсем другой завод»"))},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        assert db.query(ProductRegistryRecord).filter_by(product_id=product_id, registry=GISP).one_or_none() is None


def test_registry_poll_failure_is_explained(client, admin_token, monkeypatch):
    def refuse(registry, **_kwargs):
        raise RegistryUnavailable("Реестр ответил 403 — загрузите выгрузку файлом")

    monkeypatch.setattr(registry_polling, "_download", refuse)
    resp = client.post(f"/catalog/registries/{GISP}/poll", headers=_auth(admin_token))
    assert resp.status_code == 202, resp.text
    poll_id = resp.json()["id"]
    assert resp.json()["status"] == "running"

    # Опрос идёт в потоке — ждём его завершения, не дольше пяти секунд.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            poll = db.get(AdmissionRegistryPoll, uuid.UUID(poll_id))
            if poll.status != "running":
                break
        time.sleep(0.1)
    assert poll.status == "error"
    assert "403" in poll.message

    polls = {row["registry"]: row for row in client.get("/catalog/registries/polls", headers=_auth(admin_token)).json()}
    assert polls[GISP]["last_poll"]["status"] == "error"


def test_broken_registry_file_is_rejected_with_reason(client, admin_token):
    resp = client.post(
        f"/catalog/registries/{GISP}/upload",
        files={"file": ("gisp.xlsx", b"not a workbook")},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 422
    assert "XLSX" in resp.json()["detail"]
