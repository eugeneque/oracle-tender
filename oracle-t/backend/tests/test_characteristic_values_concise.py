"""Краткие значения характеристик и свежее «Описание типа» (замечания 19.11.2026)."""

from app.adapters.fgis import latest_description_type
from app.services import characteristic_values as cv


def test_duration_is_short():
    assert cv.normalise_value("Средняя наработка на отказ", "350000 ч для некоторых корпусов") == "350000 ч"
    assert cv.normalise_value("Срок службы", "30 лет") == "30 лет"


def test_dimensions_label_is_dropped():
    assert cv.normalise_value("Габаритные размеры", "длина х ширина х высота, мм, не более") is None
    assert cv.normalise_value("Габаритные размеры", "236×190×106 мм") == "236×190×106 мм"


def test_family_phrase_with_counters_resolves_by_designation():
    value = (
        "35 лет для счётчиков в корпусах W31, W32, D31, SP31, "
        "48 лет для счётчиков в корпусах W37, D37, SP37"
    )
    tokens = cv.designation_tokens("МИРТЕК-32-РУ-SP31")
    assert cv.resolve_family_value(value, tokens) == "35 лет"


def test_latest_description_type_by_date_not_text():
    specs = [
        {"title": "Описание типа", "doc_uuid": "a", "order_date": "27.01.2026"},
        {"title": "Описание типа", "doc_uuid": "b", "order_date": "23.07.2026"},
        {"title": "Описание типа", "doc_uuid": "c", "order_date": "22.06.2026"},
        {"title": "Описание типа", "doc_uuid": "d"},
    ]
    assert latest_description_type(specs).doc_uuid == "b"
