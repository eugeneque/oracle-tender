"""Карточка модели по 39 параметрам файла «Параметры для ПУ» (28.09.2026)."""

import uuid

from app.db.session import SessionLocal
from app.models.manufacturer import Manufacturer
from app.seed.characteristics_data import ALL_FIELDS
from app.seed.meter_parameters import METER_PARAMETERS


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _product(client, admin_token, brand: str) -> str:
    db = SessionLocal()
    try:
        manufacturer = Manufacturer(
            legal_name=f"ООО «{brand} {uuid.uuid4().hex[:8]}»", brand_name=brand, is_mirtek=False
        )
        db.add(manufacturer)
        db.commit()
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()
    response = client.post(
        f"/manufacturers/{manufacturer_id}/products",
        json={"model_name": "Тест-1"},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_every_parameter_has_a_place_for_the_answer():
    """У каждого параметра есть поле справочника либо источник вне характеристик: реестр
    допуска (П35 ПП 719, П38 ЗАК) или каталог «Ready for Astra» (П39)."""

    known = {field for _group, field in ALL_FIELDS}
    for parameter in METER_PARAMETERS:
        assert all(name in known for name in parameter.fields), parameter.no
        assert parameter.fields or parameter.no in (35, 38, 39), parameter.no


def test_card_lists_all_39_and_new_field_can_be_filled(client, admin_token, monkeypatch):
    from app.adapters import astra_compatible

    monkeypatch.setattr(astra_compatible, "load_catalog", lambda **kwargs: None)
    headers = _auth_headers(admin_token)
    product_id = _product(client, admin_token, "Тестприбор")

    put = client.put(
        f"/products/{product_id}/characteristics",
        json={"group_name": "Функциональные возможности", "field_name": "Датчик наклона", "value": "Есть"},
        headers=headers,
    )
    assert put.status_code == 200, put.text

    response = client.get(f"/products/{product_id}/meter-parameters", headers=headers)
    assert response.status_code == 200, response.text
    rows = {row["no"]: row for row in response.json()}
    # П26 (трёхпозиционное реле) бывает только у Энергомеры, Тайпита и Пульсара — у
    # остальных производителей параметр не выводится.
    assert sorted(rows) == [no for no in range(1, 40) if no != 26]

    tilt = rows[34]
    assert tilt["filled"] is True
    assert tilt["fields"][0]["characteristic"]["value"] == "Есть"

    assert rows[33]["filled"] is False  # ВЧ-поле не заполнено
    assert rows[33]["fields"][0]["characteristic"] is None

    # ПП 719 и ЗАК — из реестров допуска: пока не проверялось, это не ответ.
    assert rows[35]["filled"] is False
    assert "не проверялось" in rows[35]["facts"][0]["text"]
    assert rows[38]["facts"][0]["tone"] == "muted"

    assert "недоступен" in rows[39]["facts"][0]["text"]


def test_three_position_relay_shown_for_listed_brands(client, admin_token, monkeypatch):
    from app.adapters import astra_compatible

    monkeypatch.setattr(astra_compatible, "load_catalog", lambda **kwargs: None)
    product_id = _product(client, admin_token, "Энергомера")
    rows = {
        row["no"]: row
        for row in client.get(
            f"/products/{product_id}/meter-parameters", headers=_auth_headers(admin_token)
        ).json()
    }
    assert 26 in rows
    assert len(rows) == 39
