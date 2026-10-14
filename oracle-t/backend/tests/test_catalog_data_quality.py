"""Качество характеристик в карточке модели (замечания тестировщика 13.10.2026).

Проверяется то, из-за чего карточка показывала «2 фазы» у однофазного счётчика, «A1R1»
вместо «1/1», размеры всего семейства в одной строке и обозначение чужого исполнения:
нормализация значений, выбор варианта своей модели, приоритет источников, привязка полного
обозначения, разбор выгрузки ГИСП и ремонт уже сохранённого."""

from __future__ import annotations

import io
import re
import uuid

from app.adapters import admission_registries
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ProductDataSource,
)
from app.seed.characteristics_data import resolve_field
from app.services import catalog_repair, characteristic_extraction, characteristic_values as cv
from app.services.registry_modifications import modification_belongs_to


# --------------------------------------------------------------------------- значения


def test_accuracy_class_is_shown_as_active_slash_reactive():
    assert cv.normalise_value("Класс точности", "A1R1") == "1/1"
    assert cv.normalise_value("Класс точности", "1/2") == "1/2"


def test_phase_count_accepts_only_one_or_three():
    # «2» — код из обозначения CE2**2**10, а не число фаз.
    assert cv.normalise_value("Количество фаз", "2") is None
    assert cv.normalise_value("Количество фаз", "однофазный") == "1"
    assert cv.normalise_value("Количество фаз", "однофазные и трехфазные") is None


def test_numeric_field_without_digits_is_dropped():
    assert cv.normalise_value("Максимальный ток", "Iмакс") is None
    assert cv.normalise_value("Максимальный ток", "80A") == "80 А"


def test_family_value_resolved_to_own_variant():
    value = "110×90×73 мм для корпуса C10; 120×180×74 мм для корпуса C36"
    assert cv.is_family_level(value)
    assert cv.resolve_family_value(value, cv.designation_tokens("CE210 C36")) == "120×180×74 мм"


def test_designation_code_is_not_a_value():
    assert cv.looks_like_designation_code("L1", "CE210 C10.746.L1F", "Интерфейсы")
    # Класс точности сам пишется кодом — его нормализует normalise_value, а не удаляет фильтр.
    assert not cv.looks_like_designation_code("A1R1", "МИРТЕК-12-РУ-SP17-A1R1", "Класс точности")


def test_site_feature_list_spread_into_fields():
    found, leftover = cv.split_feature_list("Интерфейсы", "RS-485, оптопорт, NB-IoT")
    assert {name for name, _ in found} == {"RS-485", "Оптический порт", "NB-IoT"}
    assert leftover is None


# --------------------------------------------------------------------------- приоритет и обозначение


def _manufacturer(db) -> Manufacturer:
    manufacturer = Manufacturer(legal_name=f"ООО «Качество {uuid.uuid4().hex[:8]}»", is_mirtek=False)
    db.add(manufacturer)
    db.flush()
    return manufacturer


def _product(db, manufacturer, **kwargs) -> Product:
    product = Product(manufacturer_id=manufacturer.id, model_name=kwargs.pop("model_name", "CE210 C36"), **kwargs)
    db.add(product)
    db.flush()
    return product


def _value(db, product, field_name):
    return db.query(ProductCharacteristic).filter_by(product_id=product.id, field_name=field_name).one_or_none()


def _field(name: str) -> tuple[str, str]:
    resolved = resolve_field("", name)
    assert resolved, name
    return resolved


def test_fgis_family_text_does_not_overwrite_manufacturer_site(db_session):
    manufacturer = _manufacturer(db_session)
    product = _product(db_session, manufacturer, model_code="CE210 C36", data_source=ProductDataSource.MANUFACTURER_SITE.value)
    group, field = _field("Количество фаз")
    db_session.add(
        ProductCharacteristic(
            product_id=product.id, group_name=group, field_name=field, value="1",
            source=CharacteristicSource.MANUFACTURER_SITE.value,
        )
    )
    db_session.flush()

    outcome = characteristic_extraction.ExtractionOutcome()
    characteristic_extraction.apply_characteristics(
        db_session,
        product,
        [characteristic_extraction.ExtractedCharacteristic(group_name=group, field_name=field, value="3", confidence=0.9)],
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        outcome=outcome,
    )
    row = _value(db_session, product, field)
    assert row.value == "1"
    assert row.source == CharacteristicSource.MANUFACTURER_SITE.value


def test_ai_values_are_normalised_and_flagged_for_review(db_session):
    manufacturer = _manufacturer(db_session)
    product = _product(db_session, manufacturer, data_source=ProductDataSource.FGIS.value)
    group, field = _field("Класс точности")
    outcome = characteristic_extraction.ExtractionOutcome()
    characteristic_extraction.apply_characteristics(
        db_session,
        product,
        [characteristic_extraction.ExtractedCharacteristic(group_name=group, field_name=field, value="A1R1", confidence=0.8)],
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        outcome=outcome,
    )
    row = _value(db_session, product, field)
    assert row.value == "1/1"
    assert row.ai_extracted is True


def test_foreign_sample_designation_does_not_belong_to_site_model():
    site = Product(
        model_name="CE210 C10.746.B SPDS", model_code="CE210 C10.746.B SPDS",
        data_source=ProductDataSource.MANUFACTURER_SITE.value,
    )
    assert not modification_belongs_to(site, "CE210 C10.746.L1F")
    mirtek = Product(
        model_name="МИРТЕК-12-РУ-SP17", model_code="МИРТЕК-12-РУ-SP17",
        data_source=ProductDataSource.MANUFACTURER_SITE.value,
    )
    assert modification_belongs_to(mirtek, "МИРТЕК-12-РУ-SP17-A1R1-230-5-100A-ST")


# --------------------------------------------------------------------------- выгрузка ГИСП


def _xlsx(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_gisp_xlsx_keeps_only_electricity_meters():
    content = _xlsx(
        [
            ["Выгрузка реестра"],
            ["Номер реестровой записи", "Наименование продукции", "ОКПД2", "Наименование организации", "Дата внесения в реестр"],
            ["10001\\1\\2024", "Счётчик электрической энергии МИРТЕК-12-РУ", "26.51.63.130", "ООО «МИРТЕК»", "01.02.2024"],
            ["10002\\1\\2024", "Насос центробежный", "28.13.14.110", "АО «Насосы»", "01.02.2024"],
        ]
    )
    relevant = re.compile(r"26\.51\.63|сч[её]тчик", re.IGNORECASE)
    rows, total = admission_registries.parse_xlsx(content, relevant=relevant)
    assert total == 2
    assert [row.record_number for row in rows] == ["10001\\1\\2024"]
    assert rows[0].organization == "ООО «МИРТЕК»"
    assert rows[0].issued_at.isoformat() == "2024-02-01"


def test_not_a_registry_file_explains_itself():
    content = _xlsx([["Просто", "таблица"], ["1", "2"]])
    try:
        admission_registries.parse_xlsx(content, relevant=re.compile("x"))
    except admission_registries.RegistryUnavailable as exc:
        assert "ПП 719" in str(exc)
    else:
        raise AssertionError("ожидалась RegistryUnavailable")


# --------------------------------------------------------------------------- ремонт


def test_repair_preview_changes_nothing_and_apply_fixes(db_session, admin_user, monkeypatch):
    monkeypatch.setattr(catalog_repair, "_refresh_sites_in_background", lambda *args, **kwargs: None)
    manufacturer = _manufacturer(db_session)
    product = _product(db_session, manufacturer, model_code="CE210 C36", data_source=ProductDataSource.MANUFACTURER_SITE.value)
    phases_group, phases = _field("Количество фаз")
    accuracy_group, accuracy = _field("Класс точности")
    db_session.add_all(
        [
            ProductCharacteristic(
                product_id=product.id, group_name=phases_group, field_name=phases, value="2",
                source=CharacteristicSource.FGIS_DESCRIPTION_TYPE.value, ai_extracted=True,
            ),
            ProductCharacteristic(
                product_id=product.id, group_name=accuracy_group, field_name=accuracy, value="A1R1",
                source=CharacteristicSource.MANUFACTURER_SITE.value,
            ),
        ]
    )
    db_session.commit()
    product_id = product.id

    preview = catalog_repair.repair(db_session, apply=False, actor=admin_user)
    assert preview.products_affected >= 1
    assert _value(db_session, db_session.get(Product, product_id), accuracy).value == "A1R1"

    catalog_repair.repair(db_session, apply=True, actor=admin_user)
    product = db_session.get(Product, product_id)
    assert _value(db_session, product, phases) is None
    assert _value(db_session, product, accuracy).value == "1/1"
