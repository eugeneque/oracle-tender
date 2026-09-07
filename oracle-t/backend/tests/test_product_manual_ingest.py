"""Тесты загрузки руководств по эксплуатации (`app/services/product_manual_ingest.py`).

Сеть в тестах не трогается: и `robots.txt`, и сам файл руководства подменяются. Проверяется
поведение, ради которого сервис написан, — что чужие правила соблюдаются, что повторный
запуск не переплачивает за уже разобранные документы и что текст руководства действительно
доходит до экстракции характеристик.
"""

from __future__ import annotations

import uuid

import pytest

import app.services.product_manual_ingest as ingest_module
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
)
from app.models.user import User
from app.services.product_manual_ingest import RobotsGate, ingest_manuals


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


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


def _product_with_manual(db_session, manufacturer, url: str, suffix: str = "") -> Product:
    product = Product(
        manufacturer_id=manufacturer.id,
        model_name=f"Модель {suffix}{uuid.uuid4().hex[:4]}",
    )
    db_session.add(product)
    db_session.flush()
    db_session.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name="Документация",
            field_name="Ссылка на руководство",
            value=url,
            source=CharacteristicSource.MANUFACTURER_SITE.value,
        )
    )
    db_session.commit()
    return product


@pytest.fixture
def no_network(monkeypatch):
    """Всё разрешено robots.txt, руководство скачивается, разбор моделью не вызывается."""

    monkeypatch.setattr(RobotsGate, "_load", lambda self, origin: None)
    monkeypatch.setattr(
        ingest_module, "_download_text", lambda client, url: "Класс точности 1,0"
    )


def _fake_reading(monkeypatch, seen: list[str], *, saved: int = 3):
    """Подменяет обращение к модели: собирает тексты, которые до неё дошли."""

    from app.services import characteristic_extraction

    class _Reading:
        characteristics = ["одна характеристика"]
        chunks_processed = 1
        chunks_failed = 0

    def fake_read(db, *, text, document_source):
        seen.append(text)
        return _Reading()

    def fake_apply(db, product, characteristics, *, characteristic_source, outcome):
        outcome.saved += saved

    monkeypatch.setattr(characteristic_extraction, "read_characteristics", fake_read)
    monkeypatch.setattr(characteristic_extraction, "apply_characteristics", fake_apply)


def test_manual_text_reaches_extraction(db_session, admin_user: User, monkeypatch, no_network):
    manufacturer = _manufacturer(db_session)
    _product_with_manual(db_session, manufacturer, "https://example.test/manual.pdf")
    seen: list[str] = []
    _fake_reading(monkeypatch, seen)

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=5)

    assert seen == ["Класс точности 1,0"]
    assert outcome.processed == 1
    assert outcome.characteristics_saved == 3


def test_one_manual_serves_the_whole_family(db_session, admin_user: User, monkeypatch, no_network):
    """Руководство «Меркурий 200/201» описывает десяток исполнений — платить за его разбор
    столько раз, сколько в семействе позиций, незачем."""

    manufacturer = _manufacturer(db_session)
    shared = "https://example.test/manual-200-201.pdf"
    for index in range(3):
        _product_with_manual(db_session, manufacturer, shared, suffix=str(index))
    seen: list[str] = []
    _fake_reading(monkeypatch, seen)

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=5)

    assert len(seen) == 1, "модель должна вызываться один раз на документ, а не на позицию"
    assert outcome.processed == 1
    assert outcome.reused_documents == 2
    # Характеристики при этом получают все три модели семейства.
    assert outcome.characteristics_saved == 9


def test_limit_counts_documents_not_models(db_session, admin_user: User, monkeypatch, no_network):
    """Предел в один документ не должен обрывать раздачу уже разобранного руководства."""

    manufacturer = _manufacturer(db_session)
    shared = "https://example.test/manual-family.pdf"
    for index in range(3):
        _product_with_manual(db_session, manufacturer, shared, suffix=str(index))
    _fake_reading(monkeypatch, [])

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=1)

    assert outcome.processed == 1
    assert outcome.reused_documents == 2


def test_robots_disallow_is_respected(db_session, admin_user: User, monkeypatch):
    """У Энергомеры раздел с документацией закрыт к обходу — ссылку храним, файл не берём."""

    manufacturer = _manufacturer(db_session)
    _product_with_manual(db_session, manufacturer, "https://example.test/documentations/re.pdf")
    monkeypatch.setattr(
        ingest_module.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse("User-agent: *\nDisallow: /documentations/"),
    )
    monkeypatch.setattr(
        ingest_module,
        "_download_text",
        lambda client, url: pytest.fail("файл не должен скачиваться"),
    )

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=5)

    assert outcome.skipped_by_robots == 1
    assert outcome.processed == 0


def test_missing_robots_file_does_not_block_download(db_session, admin_user: User, monkeypatch):
    """Сайта без robots.txt это касается тоже: 404 — не запрет."""

    manufacturer = _manufacturer(db_session)
    _product_with_manual(db_session, manufacturer, "https://example.test/manual.pdf")
    monkeypatch.setattr(
        ingest_module.httpx, "get", lambda *args, **kwargs: _FakeResponse("", status_code=404)
    )
    monkeypatch.setattr(ingest_module, "_download_text", lambda client, url: "текст")

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=5, use_ai=False)

    assert outcome.processed == 1
    assert outcome.skipped_by_robots == 0


def test_already_parsed_manual_is_not_downloaded_again(
    db_session, admin_user: User, no_network
):
    """Повторный запуск не должен платить за те же документы второй раз."""

    manufacturer = _manufacturer(db_session)
    product = _product_with_manual(db_session, manufacturer, "https://example.test/manual.pdf")
    db_session.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name="Электрические характеристики",
            field_name="Класс точности",
            value="1,0",
            source=CharacteristicSource.USER_MANUAL.value,
        )
    )
    db_session.commit()

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=5, use_ai=False)

    assert outcome.skipped_have_data == 1
    assert outcome.processed == 0


def test_product_without_manual_link_is_skipped(db_session, admin_user: User, no_network):
    manufacturer = _manufacturer(db_session)
    product = Product(manufacturer_id=manufacturer.id, model_name="Без руководства")
    db_session.add(product)
    db_session.commit()

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=5, use_ai=False)

    assert outcome.skipped_no_link == 1


def test_limit_caps_one_run(db_session, admin_user: User, no_network):
    """Руководств у производителя бывают сотни, а каждое стоит запросов к модели."""

    manufacturer = _manufacturer(db_session)
    for index in range(4):
        _product_with_manual(db_session, manufacturer, f"https://example.test/m{index}.pdf")

    outcome = ingest_manuals(db_session, manufacturer, actor=admin_user, limit=2, use_ai=False)

    assert outcome.processed == 2
