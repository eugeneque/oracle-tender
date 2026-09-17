"""Тесты справочника актуальности документов (`app/services/document_registry_service.py`).

Сеть не трогается: отпечатки файлов подменяются. Проверяется то, ради чего справочник
заведён, — что ссылки из справочника продукции и кодов СИ попадают в реестр с датой, что
повторная сверка замечает переиздание документа и пишет об этом отчёт, и что пропавшая
ссылка гасит строку, а не удаляет её.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

import app.services.document_registry_service as registry
from app.models.catalog_document import (
    CatalogDocument,
    CatalogDocumentKind,
    DocumentCheckStatus,
    DocumentDateSource,
)
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    SiType,
    SiTypeSource,
)
from app.models.notification import Notification, NotificationTrigger
from app.core.timezones import now_msk
from app.services.document_registry_service import Fingerprint, run_check
from app.services.product_manual_ingest import RobotsGate


def _manufacturer(db_session) -> Manufacturer:
    manufacturer = Manufacturer(
        legal_name=f"ООО «Тест {uuid.uuid4().hex[:8]}»",
        brand_name="Тест",
        website="https://example.test",
        is_mirtek=False,
    )
    db_session.add(manufacturer)
    db_session.flush()
    return manufacturer


def _product(db_session, manufacturer, links: dict[str, str]) -> Product:
    product = Product(
        manufacturer_id=manufacturer.id, model_name=f"Модель {uuid.uuid4().hex[:4]}"
    )
    db_session.add(product)
    db_session.flush()
    for field_name, url in links.items():
        db_session.add(
            ProductCharacteristic(
                product_id=product.id,
                group_name="Документация",
                field_name=field_name,
                value=url,
                source=CharacteristicSource.MANUFACTURER_SITE.value,
            )
        )
    db_session.commit()
    return product


@pytest.fixture
def fingerprints(monkeypatch) -> dict[str, Fingerprint]:
    """Отпечатки по адресам вместо HTTP; robots.txt всё разрешает, пауз между запросами нет."""

    table: dict[str, Fingerprint] = {}
    monkeypatch.setattr(registry, "_fetch_fingerprint", lambda url: table.get(url, Fingerprint(error="HTTP 404")))
    monkeypatch.setattr(RobotsGate, "_load", lambda self, origin: None)
    monkeypatch.setattr(registry, "_throttle", lambda url, last, gate: None)
    return table


def _documents(db_session, manufacturer) -> list[CatalogDocument]:
    return list(
        db_session.query(CatalogDocument)
        .filter(CatalogDocument.manufacturer_id == manufacturer.id)
        .order_by(CatalogDocument.kind)
    )


def test_registry_is_filled_from_catalog_links_and_si_types(db_session, fingerprints):
    manufacturer = _manufacturer(db_session)
    _product(
        db_session,
        manufacturer,
        {
            "Ссылка на руководство": "https://example.test/manual.pdf",
            "Ссылка на сертификат": "https://example.test/cert.pdf",
            # Одна и та же ссылка в двух полях — как у МИРТЕК (комбинированный PDF).
            "Ссылка на описание типа": "https://example.test/cert.pdf",
            # Не ссылка — в реестр не идёт.
            "Ссылка на декларацию": "нет",
        },
    )
    si_type = SiType(
        manufacturer_id=manufacturer.id,
        si_code=f"61891-{uuid.uuid4().hex[:2]}",
        source=SiTypeSource.AUTO_SEARCH.value,
        description_type_url="https://fgis.test/doc/1",
        description_type_version="3",
    )
    db_session.add(si_type)
    db_session.commit()

    fingerprints["https://example.test/manual.pdf"] = Fingerprint(
        etag='"v1"', last_modified="Mon, 02 Mar 2026 10:00:00 GMT", last_modified_date=date(2026, 3, 2)
    )
    fingerprints["https://example.test/cert.pdf"] = Fingerprint(content_length=1000)

    outcome = run_check(db_session, manufacturer=manufacturer)

    docs = {(d.kind, d.url): d for d in _documents(db_session, manufacturer)}
    assert set(docs) == {
        ("manual", "https://example.test/manual.pdf"),
        ("certificate", "https://example.test/cert.pdf"),
        ("description_type", "https://example.test/cert.pdf"),
        ("description_type", "https://fgis.test/doc/1"),
    }
    # Все четыре — новые, ни одно не «изменилось»: первая сверка только снимает отпечаток.
    assert len(outcome.new) == 4
    assert outcome.changed == [] and outcome.removed == [] and outcome.unavailable == []
    # Один адрес — один запрос, сколько бы строк на него ни ссылалось.
    assert outcome.checked == 3
    assert outcome.registry_size == 4

    manual = docs[("manual", "https://example.test/manual.pdf")]
    assert manual.document_date == date(2026, 3, 2)
    assert manual.document_date_source == DocumentDateSource.LAST_MODIFIED.value
    assert manual.check_status == DocumentCheckStatus.OK.value

    cert = docs[("certificate", "https://example.test/cert.pdf")]
    # Сервер не сообщил дату файла — датой стал день фиксации, и это видно по источнику.
    assert cert.document_date == now_msk().date()
    assert cert.document_date_source == DocumentDateSource.OBSERVED.value

    fgis = docs[("description_type", "https://fgis.test/doc/1")]
    assert fgis.version_label == "3"
    assert fgis.document_date_source == DocumentDateSource.FGIS_VERSION.value
    assert fgis.si_type_id == si_type.id

    # Первый прогон — тоже обновление справочника: появились документы, есть о чём отчитаться.
    assert outcome.notification is not None
    assert outcome.notification.trigger == NotificationTrigger.DOCUMENTS_UPDATED.value
    assert "появилось 4" in outcome.notification.subject


def test_recheck_detects_reissued_document_and_reports_it(db_session, fingerprints):
    manufacturer = _manufacturer(db_session)
    product = _product(
        db_session, manufacturer, {"Ссылка на руководство": "https://example.test/manual.pdf"}
    )
    fingerprints["https://example.test/manual.pdf"] = Fingerprint(
        etag='"v1"', last_modified="Mon, 02 Mar 2026 10:00:00 GMT", last_modified_date=date(2026, 3, 2)
    )
    run_check(db_session, manufacturer=manufacturer)

    # Ничего не поменялось — тихая неделя, письма нет.
    quiet = run_check(db_session, manufacturer=manufacturer)
    assert not quiet.has_updates
    assert quiet.notification is None

    # Производитель переиздал руководство.
    fingerprints["https://example.test/manual.pdf"] = Fingerprint(
        etag='"v2"', last_modified="Fri, 11 Sep 2026 08:00:00 GMT", last_modified_date=date(2026, 9, 11)
    )
    outcome = run_check(db_session, manufacturer=manufacturer)

    assert len(outcome.changed) == 1
    (document,) = _documents(db_session, manufacturer)
    assert document.product_id == product.id
    assert document.document_date == date(2026, 9, 11)
    assert document.change_count == 1
    assert document.changed_at is not None
    assert outcome.notification is not None
    assert "изменилось 1" in outcome.notification.subject
    assert product.model_name in outcome.notification.body
    assert "11.09.2026" in outcome.notification.body

    # Отчёт остался в журнале уведомлений как отчёт системы.
    stored = db_session.get(Notification, outcome.notification.id)
    assert stored is not None and stored.trigger == "documents_updated"


def test_missing_link_deactivates_document_and_unavailable_file_is_reported_once(
    db_session, fingerprints
):
    manufacturer = _manufacturer(db_session)
    product = _product(
        db_session,
        manufacturer,
        {
            "Ссылка на руководство": "https://example.test/manual.pdf",
            "Ссылка на паспорт": "https://example.test/gone.pdf",
        },
    )
    fingerprints["https://example.test/manual.pdf"] = Fingerprint(etag='"v1"')
    first = run_check(db_session, manufacturer=manufacturer)
    assert len(first.unavailable) == 1  # паспорт отвечает 404 — сообщается
    second = run_check(db_session, manufacturer=manufacturer)
    assert second.unavailable == []  # …но не каждую неделю заново

    # Ссылка на паспорт пропала из справочника — обход сайта её больше не нашёл.
    db_session.query(ProductCharacteristic).filter(
        ProductCharacteristic.product_id == product.id,
        ProductCharacteristic.field_name == "Ссылка на паспорт",
    ).delete()
    db_session.commit()

    outcome = run_check(db_session, manufacturer=manufacturer)
    assert len(outcome.removed) == 1
    passport = next(d for d in _documents(db_session, manufacturer) if d.kind == "passport")
    assert passport.is_active is False  # погашена, не удалена
    assert outcome.registry_size == 1

    listed = registry.list_documents(db_session, manufacturer_id=manufacturer.id)
    assert [row["kind"] for row in listed] == ["manual", "passport"]  # погасшие — в конце
    assert listed[0]["model_name"] == product.model_name

    summary = registry.summary(db_session, manufacturer_id=manufacturer.id)
    assert summary["total"] == 1
    assert summary["last_checked_at"] is not None


def test_new_url_for_same_document_is_a_change_not_a_pair_of_events(db_session, fingerprints):
    manufacturer = _manufacturer(db_session)
    product = _product(
        db_session, manufacturer, {"Ссылка на руководство": "https://example.test/manual-v1.pdf"}
    )
    fingerprints["https://example.test/manual-v1.pdf"] = Fingerprint(etag='"v1"')
    fingerprints["https://example.test/manual-v2.pdf"] = Fingerprint(etag='"v2"')
    run_check(db_session, manufacturer=manufacturer)

    link = db_session.query(ProductCharacteristic).filter(
        ProductCharacteristic.product_id == product.id
    ).one()
    link.value = "https://example.test/manual-v2.pdf"
    db_session.commit()

    outcome = run_check(db_session, manufacturer=manufacturer)
    assert outcome.new == [] and outcome.removed == []
    assert len(outcome.changed) == 1
    assert "адрес изменился" in outcome.changed[0].detail
    (document,) = _documents(db_session, manufacturer)
    assert document.kind == CatalogDocumentKind.MANUAL.value
    assert document.url == "https://example.test/manual-v2.pdf"
    assert document.etag == '"v2"'


def test_fingerprint_comparison_prefers_strong_signals():
    document = CatalogDocument(
        etag='"a"', last_modified_header="Mon, 02 Mar 2026 10:00:00 GMT", content_length=100
    )
    # ETag совпал — размер и дата уже не важны.
    assert Fingerprint(etag='"a"', content_length=999).differs_from(document) is None
    assert Fingerprint(etag='"b"').differs_from(document) is not None
    # ETag с одной стороны нет — решает Last-Modified.
    assert (
        Fingerprint(last_modified="Mon, 02 Mar 2026 10:00:00 GMT", content_length=999).differs_from(
            document
        )
        is None
    )
    # Только размер — слабый признак, но единственный.
    assert Fingerprint(content_length=100).differs_from(document) is None
    assert Fingerprint(content_length=101).differs_from(document) is not None


def test_documents_endpoints(client, admin_token, fingerprints):
    headers = {"Authorization": f"Bearer {admin_token}"}
    manufacturers = client.get("/manufacturers", headers=headers).json()
    manufacturer_id = manufacturers[0]["id"]

    response = client.get(f"/catalog/documents?manufacturer_id={manufacturer_id}", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    response = client.get("/catalog/documents/summary", headers=headers)
    assert response.status_code == 200
    assert {"total", "last_checked_at", "changed_recently", "unavailable"} <= set(response.json())

    response = client.post(
        f"/catalog/documents/check?manufacturer_id={uuid.uuid4()}", headers=headers
    )
    assert response.status_code == 404


def test_queue_handler_runs_check_for_manufacturer(db_session, fingerprints):
    """Ручной запуск из каталога идёт через очередь справочника — обработчик должен уметь
    выполнить сверку по задаче с одним лишь `manufacturer_id`."""

    from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason

    manufacturer = _manufacturer(db_session)
    _product(db_session, manufacturer, {"Ссылка на руководство": "https://example.test/manual.pdf"})
    fingerprints["https://example.test/manual.pdf"] = Fingerprint(etag='"v1"')

    task = CatalogLookupTask(
        adapter_key=registry.ADAPTER_KEY,
        reason=CatalogQueueReason.MANUAL.value,
        manufacturer_id=manufacturer.id,
    )
    db_session.add(task)
    db_session.commit()

    outcome = registry.handle_task(db_session, task)
    assert "появилось 1" in outcome.message
    assert not outcome.needs_review
