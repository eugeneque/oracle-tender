"""Тесты отбора моделей в карточку производителя (`app/services/product_relevance.py`).

Проверяется то, ради чего отбор появился: в сравнение с требованиями закупки должны уходить
модели, которые этим требованиям отвечают, а не первые по алфавиту. У Энергомеры 221 модель,
а в карточку помещается шесть — при алфавитном порядке подходящий прибор в сравнение мог не
попасть вовсе, и матрица соответствия честно ставила «нет данных» на требование, которому
отвечает конкретный счётчик из справочника.
"""

from __future__ import annotations

import uuid

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, ProductStatus
from app.models.source import Source
from app.models.tender import Tender
from app.services.product_relevance import select_products_for_context


def _manufacturer(db_session) -> Manufacturer:
    manufacturer = Manufacturer(
        legal_name=f"ООО «Тест {uuid.uuid4().hex[:8]}»", brand_name="Тест", is_mirtek=False
    )
    db_session.add(manufacturer)
    db_session.flush()
    return manufacturer


def _tender(db_session) -> Tender:
    suffix = uuid.uuid4().hex[:8]
    source = Source(
        key=f"rel_{suffix}", name="Источник", url=f"https://example.test/{suffix}", type="eis"
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"R-{suffix}", title="Закупка")
    db_session.add(tender)
    db_session.flush()
    return tender


def _product(db_session, manufacturer, name, *, code=None, phases=None, values=(), status=None):
    product = Product(
        manufacturer_id=manufacturer.id,
        model_name=name,
        model_code=code,
        status=status or ProductStatus.ACTIVE.value,
    )
    db_session.add(product)
    db_session.flush()
    if phases:
        db_session.add(
            ProductCharacteristic(
                product_id=product.id,
                group_name="Электрические характеристики",
                field_name="Количество фаз",
                value=phases,
                source="manufacturer_site",
            )
        )
    for field_name, value in values:
        db_session.add(
            ProductCharacteristic(
                product_id=product.id,
                group_name="Электрические характеристики",
                field_name=field_name,
                value=value,
                source="manufacturer_site",
            )
        )
    db_session.flush()
    return product


def _requirements(db_session, tender, *texts) -> list[Requirement]:
    result = []
    for text in texts:
        requirement = Requirement(tender_id=tender.id, text=text, criticality="critical")
        db_session.add(requirement)
        result.append(requirement)
    db_session.flush()
    return result


def test_matching_phases_win_over_alphabet(db_session):
    """Ради этого отбор и появился: «АААА» стоит первым по алфавиту, но закупка трёхфазная."""

    manufacturer = _manufacturer(db_session)
    tender = _tender(db_session)
    _product(db_session, manufacturer, "АААА однофазный", phases="1")
    wanted = _product(db_session, manufacturer, "ЯЯЯЯ трёхфазный", phases="3")
    requirements = _requirements(db_session, tender, "Счётчик трёхфазный прямого включения")

    selected = select_products_for_context(db_session, manufacturer, requirements, limit=1)

    assert [item.product.id for item in selected] == [wanted.id]


def test_model_named_in_requirements_is_taken_first(db_session):
    """Закупка, называющая прибор по имени, должна получить именно его — что бы ни говорили
    остальные признаки."""

    manufacturer = _manufacturer(db_session)
    tender = _tender(db_session)
    _product(db_session, manufacturer, "Счётчик A", phases="3", values=[("Класс точности", "1")])
    named = _product(
        db_session, manufacturer, "Счётчик CE208 S31", code="CE208", phases="1"
    )
    requirements = _requirements(
        db_session, tender, "Прибор учёта типа CE208 или эквивалент, трёхфазный"
    )

    selected = select_products_for_context(db_session, manufacturer, requirements, limit=1)

    assert [item.product.id for item in selected] == [named.id]


def test_products_without_characteristics_are_left_out(db_session):
    """Модель без единой характеристики занимает место в карточке и ничего не сообщает."""

    manufacturer = _manufacturer(db_session)
    tender = _tender(db_session)
    _product(db_session, manufacturer, "Пустая модель")
    described = _product(db_session, manufacturer, "Описанная модель", phases="1")
    requirements = _requirements(db_session, tender, "Однофазный счётчик")

    selected = select_products_for_context(db_session, manufacturer, requirements, limit=5)

    assert [item.product.id for item in selected] == [described.id]


def test_manufacturer_without_any_characteristics_still_appears(db_session):
    """Иначе производитель, чей каталог ещё не наполнен, исчез бы из матрицы целиком."""

    manufacturer = _manufacturer(db_session)
    tender = _tender(db_session)
    _product(db_session, manufacturer, "Модель без характеристик")
    requirements = _requirements(db_session, tender, "Однофазный счётчик")

    selected = select_products_for_context(db_session, manufacturer, requirements, limit=5)

    assert len(selected) == 1
    assert selected[0].characteristics == []


def test_discontinued_model_loses_to_active_one(db_session):
    manufacturer = _manufacturer(db_session)
    tender = _tender(db_session)
    active = _product(db_session, manufacturer, "Модель A", phases="1")
    _product(
        db_session,
        manufacturer,
        "Модель B",
        phases="1",
        status=ProductStatus.DISCONTINUED.value,
    )
    requirements = _requirements(db_session, tender, "Однофазный счётчик")

    selected = select_products_for_context(db_session, manufacturer, requirements, limit=1)

    assert [item.product.id for item in selected] == [active.id]


def test_matching_values_raise_the_score(db_session):
    manufacturer = _manufacturer(db_session)
    tender = _tender(db_session)
    _product(db_session, manufacturer, "Модель A", values=[("Номинальное напряжение", "230 В")])
    wanted = _product(
        db_session,
        manufacturer,
        "Модель B",
        values=[("Номинальное напряжение", "3х230/400 В"), ("Интерфейс", "RS-485")],
    )
    requirements = _requirements(
        db_session, tender, "Напряжение 3х230/400 В, интерфейс RS-485"
    )

    selected = select_products_for_context(db_session, manufacturer, requirements, limit=1)

    assert [item.product.id for item in selected] == [wanted.id]


def test_selection_without_requirements_keeps_alphabetical_order(db_session):
    """Без требований поведение прежнее — по названию: отбор нечем направлять."""

    manufacturer = _manufacturer(db_session)
    _product(db_session, manufacturer, "Модель B", phases="1")
    _product(db_session, manufacturer, "Модель A", phases="1")

    selected = select_products_for_context(db_session, manufacturer, [], limit=2)

    assert [item.product.model_name for item in selected] == ["Модель A", "Модель B"]
