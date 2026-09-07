"""Тесты CSV-импорта каталога (раздел 5.3 ТЗ, п.4 алгоритма — Этап 4)."""

from __future__ import annotations

import uuid

import pytest

from app.db.session import SessionLocal
from app.models.manufacturer import Manufacturer, Product, SiType
from app.models.user import User
from app.services.catalog_import import import_catalog_csv


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def actor(db) -> User:
    return db.query(User).filter(User.role == "admin").first()


@pytest.fixture()
def manufacturer(db) -> Manufacturer:
    m = Manufacturer(
        legal_name=f'ООО «Импорт {uuid.uuid4().hex[:8]}»', brand_name=f"Бренд{uuid.uuid4().hex[:6]}"
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_import_creates_si_types_and_products(db, actor, manufacturer):
    csv_text = (
        "manufacturer,si_code,model,device_type\n"
        f"{manufacturer.legal_name},77777-20,Модель-А,счётчик эл.энергии\n"
        f"{manufacturer.legal_name},77778-20,Модель-Б,счётчик эл.энергии\n"
    )

    outcome = import_catalog_csv(db, content=csv_text.encode("utf-8"), actor=actor)

    assert outcome.errors == []
    assert outcome.si_types_created == 2
    assert outcome.products_created == 2

    si_types = db.query(SiType).filter_by(manufacturer_id=manufacturer.id).all()
    # Импорт — данные от человека: приоритет над автопоиском (раздел 5.3 ТЗ)
    assert all(s.source == "import" and s.verified_by_user for s in si_types)
    products = db.query(Product).filter_by(manufacturer_id=manufacturer.id).all()
    assert {p.model_name for p in products} == {"Модель-А", "Модель-Б"}
    assert all(p.si_type_id is not None for p in products)


def test_import_accepts_russian_headers_and_semicolon_delimiter(db, actor, manufacturer):
    """Excel в русской локали сохраняет CSV с ';' и русскими заголовками — заставлять
    пользователя перекодировать/переименовывать колонки руками не нужно."""

    csv_text = (
        "Производитель;Код СИ;Модель\n" f"{manufacturer.brand_name};55555-19;Модель-В\n"
    )

    outcome = import_catalog_csv(db, content=csv_text.encode("utf-8"), actor=actor)

    assert outcome.errors == []
    assert outcome.si_types_created == 1
    assert outcome.products_created == 1


def test_import_accepts_cp1251(db, actor, manufacturer):
    csv_text = "manufacturer,si_code,model\n" f"{manufacturer.legal_name},44444-18,Модель-Г\n"
    outcome = import_catalog_csv(db, content=csv_text.encode("cp1251"), actor=actor)
    assert outcome.errors == []
    assert outcome.si_types_created == 1


def test_import_reports_unknown_manufacturer_without_aborting_rest(db, actor, manufacturer):
    """Одна плохая строка не должна отменять весь импорт (раздел 5.9 ТЗ — изоляция ошибок)."""

    csv_text = (
        "manufacturer,si_code,model\n"
        "ООО «Такого нет в справочнике»,11111-11,Модель-Х\n"
        f"{manufacturer.legal_name},22222-22,Модель-Y\n"
    )

    outcome = import_catalog_csv(db, content=csv_text.encode("utf-8"), actor=actor)

    assert len(outcome.errors) == 1
    assert "не найден" in outcome.errors[0]
    assert outcome.si_types_created == 1  # вторая строка всё равно импортирована


def test_import_is_idempotent_on_repeat(db, actor, manufacturer):
    csv_text = "manufacturer,si_code,model\n" f"{manufacturer.legal_name},33333-33,Модель-Z\n"

    first = import_catalog_csv(db, content=csv_text.encode("utf-8"), actor=actor)
    second = import_catalog_csv(db, content=csv_text.encode("utf-8"), actor=actor)

    assert first.si_types_created == 1 and first.products_created == 1
    assert second.si_types_created == 0 and second.si_types_updated == 1
    assert second.products_created == 0  # повторный импорт не плодит дубли моделей
    assert db.query(Product).filter_by(manufacturer_id=manufacturer.id).count() == 1


def test_import_rejects_file_without_required_columns(db, actor):
    outcome = import_catalog_csv(db, content=b"foo,bar\n1,2\n", actor=actor)
    assert outcome.errors and "обязательных колонок" in outcome.errors[0]
    assert outcome.si_types_created == 0


def test_import_skips_blank_trailing_rows(db, actor, manufacturer):
    csv_text = "manufacturer,si_code,model\n" f"{manufacturer.legal_name},66666-66,Модель-Q\n,,\n\n"
    outcome = import_catalog_csv(db, content=csv_text.encode("utf-8"), actor=actor)
    assert outcome.errors == []
    assert outcome.si_types_created == 1


def test_import_endpoint_requires_admin(client, admin_token):
    username = f"imp_{uuid.uuid4().hex[:8]}"
    client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "T", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    token = client.post(
        "/auth/login", json={"username": username, "password": "SomePass123"}
    ).json()["access_token"]

    resp = client.post(
        "/catalog/import",
        files={"file": ("catalog.csv", b"manufacturer,si_code\n", "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_import_endpoint_returns_outcome(client, admin_token):
    db = SessionLocal()
    try:
        m = Manufacturer(legal_name=f'ООО «Эндпоинт {uuid.uuid4().hex[:8]}»')
        db.add(m)
        db.commit()
        legal_name = m.legal_name
    finally:
        db.close()

    csv_bytes = f"manufacturer,si_code,model\n{legal_name},99999-99,Модель-API\n".encode("utf-8")
    resp = client.post(
        "/catalog/import",
        files={"file": ("catalog.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["si_types_created"] == 1
    assert body["products_created"] == 1
    assert body["errors"] == []
