"""Форм-фактор модели (фазность и способ установки) — по нему справочник раскладывает модели
по столбцам исполнений (предложение тестировщика 18.09.2026)."""

import uuid

from app.services.product_form_factor import classify


def _ff(name, code=None, mod=None, **chars):
    return classify(model_name=name, model_code=code, registry_modification=mod, characteristics=chars)


def test_characteristic_wins_over_name():
    # В характеристике могло быть ручное значение — оно важнее обозначения серии.
    ff = _ff("Меркурий 230 AM", "Меркурий 230 AM", **{"Количество фаз": "1", "Тип монтажа": "DIN-рейка ; 3 винта"})
    assert ff.phases == 1
    assert ff.mountings == ["din", "panel"]


def test_phases_from_words_and_series():
    assert _ff("Счетчик трехфазный многофункциональный").phases == 3
    assert _ff("НЕВА СП1 Сплит-счётчик, однофазный, IP65").phases == 1
    assert _ff("Милур 307.12-P-1L", "Милур 307.12-P-1L").phases == 3
    assert _ff("Меркурий 202.5", "Меркурий 202.5").phases == 1
    assert _ff("ЦЭ6807Б-Ш1", "ЦЭ6807Б-Ш1").phases == 1
    assert _ff("ЦЭ6803ВМ-Р31", "ЦЭ6803ВМ-Р31").phases == 3
    assert _ff("CE102-R5", "CE102-R5").phases == 1
    assert _ff("РиМ 489.2Х", "РиМ 489.2Х").phases == 3
    assert _ff("Трансформатор тока", "Трансформатор тока").phases is None


def test_mountings_from_texts_and_codes():
    assert _ff("X", **{"Тип монтажа": "На опору (split)"}).mountings == ["split"]
    assert _ff("X", **{"Тип монтажа": "универсальная установка"}).mountings == ["din", "panel"]
    assert _ff("X", **{"Тип корпуса": "в зависимости от исполнения: компактный, универсальный, сплит"}).mountings == [
        "split",
        "din",
        "panel",
    ]
    assert _ff("ЦЭ6807Б-Ш1", "ЦЭ6807Б-Ш1").mountings == ["panel"]
    assert _ff("ЦЭ6804 Р30", "ЦЭ6804 Р30").mountings == ["din"]
    assert _ff("РОТЕК РТМ-01 С1 (Сплит)", "РТМ-01 С1").mountings == ["split"]
    assert _ff("ФОБОС 3 Сплит", "ФОБОС 3").mountings == ["split"]
    # Код корпуса из условного обозначения реестра — последний источник.
    assert _ff("НАРТИС-И100-W115", "НАРТИС-И100-W115", "НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485").mountings == ["panel"]
    assert _ff("X", **{"Тип монтажа": "Не указано"}).mountings == []


def test_products_endpoint_returns_form_factor(client, admin_token):
    from app.db.session import SessionLocal
    from app.models.manufacturer import Manufacturer

    db = SessionLocal()
    try:
        manufacturer = Manufacturer(legal_name=f'ООО «ФФ {uuid.uuid4().hex[:8]}»', is_mirtek=False)
        db.add(manufacturer)
        db.commit()
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        f"/manufacturers/{manufacturer_id}/products",
        json={"model_name": "Счетчик трехфазный ТЕСТ-3 Сплит"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    product_id = created.json()["id"]
    client.put(
        f"/products/{product_id}/characteristics",
        json={"group_name": "Конструктивные характеристики", "field_name": "Тип монтажа", "value": "DIN-рейка ; 3 винта"},
        headers=headers,
    )
    rows = client.get(f"/manufacturers/{manufacturer_id}/products", headers=headers).json()
    assert rows[0]["phases"] == 3
    assert rows[0]["mountings"] == ["din", "panel"]


def test_body_code_in_designation_beats_series_wide_list():
    # 14.10.2026: «Описание типа» на весь номер Госреестра перечисляет корпуса всей серии —
    # МИРТЕК-12-РУ-SP17 стоял и в «Сплите», и на «DIN-рейке», и в «Щите».
    series = {"Тип монтажа": "на щиток, на DIN-рейку, на опору ЛЭП", "Тип корпуса": "W1, W3, W9, D1, D17, SP3, SP17"}
    assert _ff("МИРТЕК-12-РУ-SP17", "МИРТЕК-12-РУ-SP17", **series).mountings == ["split"]
    assert _ff("МИРТЕК-212-РУ-D17", "МИРТЕК-12-РУ-D17", **series).mountings == ["din"]
    assert _ff("НАРТИС-И100-W115", "НАРТИС-И100-W115", **series).mountings == ["panel"]
    # Обозначение из реестра (`registry_modification`) кодом корпуса не считается: в нём те же
    # буквы встречаются в кодах интерфейсов и функций.
    assert _ff("Счётчик", None, "X-1-RS485-D").mountings == []


def test_series_wide_value_is_narrowed_or_dropped():
    from app.services.product_form_factor import FIELD_BODY, FIELD_MOUNTING, model_specific_value

    series = "на щиток, на DIN-рейку, на опору ЛЭП"
    kw = {"model_code": "МИРТЕК-12-РУ-SP17", "model_name": "МИРТЕК-12-РУ-SP17"}
    assert model_specific_value(FIELD_MOUNTING, series, family_source=True, **kw) == "Сплит"
    assert model_specific_value(FIELD_BODY, "W1, D1, SP17", family_source=True, **kw) == "SP17"
    # Без кода в обозначении перечень остаётся: у записи серии он верен.
    plain = {"model_code": "МИРТЕК-101", "model_name": "МИРТЕК-101"}
    assert model_specific_value(FIELD_MOUNTING, "на щиток или на DIN-рейку", family_source=True, **plain) == "на щиток или на DIN-рейку"
    # Код есть, а его способа в перечне нет — значение «Описания типа» противоречит модели.
    assert model_specific_value(FIELD_MOUNTING, "на щиток; на DIN-рейку", family_source=True, **kw) is None
    assert model_specific_value(FIELD_MOUNTING, "Сплит", family_source=True, **kw) == "Сплит"
