"""Тесты AI-экстракции характеристик (раздел 5.3 ТЗ, Этап 4). Реальные вызовы YandexGPT
здесь не делаются — они платные и недетерминированные; вместо этого мокается
`run_structured`, а проверяется логика вокруг него: фильтрация по справочнику Приложения C,
защита ручных/подтверждённых значений, изоляция сбоя одного куска текста."""

from __future__ import annotations

import uuid

import pytest

import app.services.characteristic_extraction as extraction_module
from app.db.session import SessionLocal
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
)
from app.models.user import User
from app.services.characteristic_extraction import (
    ExtractedCharacteristic,
    ExtractionResult,
    extract_characteristics_for_product,
)
from app.services.yandex_ai_client import chunk_text


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def product(db) -> Product:
    manufacturer = Manufacturer(legal_name=f'ООО «Экстракт {uuid.uuid4().hex[:8]}»')
    db.add(manufacturer)
    db.flush()
    product = Product(manufacturer_id=manufacturer.id, model_name="Тест-1")
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@pytest.fixture()
def actor(db) -> User:
    return db.query(User).filter(User.role == "admin").first()


def _stub_extraction(monkeypatch, items: list[ExtractedCharacteristic]):
    monkeypatch.setattr(
        extraction_module,
        "run_structured",
        lambda db, **kwargs: ExtractionResult(characteristics=items),
    )


def test_extraction_saves_known_fields_unverified(db, product, actor, monkeypatch):
    _stub_extraction(
        monkeypatch,
        [
            ExtractedCharacteristic(
                group_name="Электрические характеристики",
                field_name="Номинальное напряжение",
                value="3х230/400 В",
                confidence=1.0,
            )
        ],
    )

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="Номинальное напряжение: 3х230/400 В",
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.saved == 1
    saved = db.query(ProductCharacteristic).filter_by(product_id=product.id).one()
    assert saved.value == "3х230/400 В"
    assert saved.source == "fgis_description_type"
    assert saved.verified_by_user is False  # раздел 5.3 ТЗ — ждёт проверки человеком


def test_extraction_rejects_fields_outside_appendix_c(db, product, actor, monkeypatch):
    """Модель, несмотря на явный список полей в промпте, изредка возвращает своё название —
    в каталог такое попадать не должно, иначе схема Приложения C перестаёт быть предсказуемой."""

    _stub_extraction(
        monkeypatch,
        [
            ExtractedCharacteristic(
                group_name="Электрические характеристики",
                field_name="Номинальный (максимальный) ток",  # формулировка документа, не поле каталога
                value="5(100) А",
            ),
            ExtractedCharacteristic(
                group_name="Выдуманная группа",
                field_name="Что-то ещё",
                value="значение",
            ),
        ],
    )

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="...",
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.saved == 0
    assert outcome.skipped_unknown_field == 2
    assert db.query(ProductCharacteristic).filter_by(product_id=product.id).count() == 0


def test_extraction_does_not_overwrite_manual_value(db, product, actor, monkeypatch):
    """Раздел 5.3 ТЗ: «Ручной ввод имеет приоритет перед автопоиском при конфликте»."""

    db.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name="Электрические характеристики",
            field_name="Класс точности",
            value="1 (введено вручную)",
            source=CharacteristicSource.MANUAL_ENTRY.value,
            verified_by_user=True,
        )
    )
    db.commit()

    _stub_extraction(
        monkeypatch,
        [
            ExtractedCharacteristic(
                group_name="Электрические характеристики",
                field_name="Класс точности",
                value="2 (из документа)",
            )
        ],
    )

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="Класс точности: 2",
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.saved == 0
    assert outcome.skipped_protected == 1
    kept = db.query(ProductCharacteristic).filter_by(product_id=product.id).one()
    assert kept.value == "1 (введено вручную)"


def test_extraction_updates_previous_automatic_value(db, product, actor, monkeypatch):
    """Неподтверждённое автоматическое значение, напротив, обновляется — иначе повторный
    прогон после исправления промпта/документа не имел бы смысла."""

    db.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name="Метрологические характеристики",
            field_name="Межповерочный интервал",
            value="10 лет",
            source=CharacteristicSource.FGIS_DESCRIPTION_TYPE.value,
            verified_by_user=False,
        )
    )
    db.commit()

    _stub_extraction(
        monkeypatch,
        [
            ExtractedCharacteristic(
                group_name="Метрологические характеристики",
                field_name="Межповерочный интервал",
                value="16 лет",
            )
        ],
    )

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="Межповерочный интервал: 16 лет",
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.saved == 1
    assert db.query(ProductCharacteristic).filter_by(product_id=product.id).one().value == "16 лет"


def test_extraction_isolates_failed_chunk(db, product, actor, monkeypatch):
    """Раздел 5.9 ТЗ — сбой на одном куске не должен терять уже извлечённое из остальных."""

    calls = {"n": 0}

    def flaky(db_session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("таймаут модели")
        return ExtractionResult(
            characteristics=[
                ExtractedCharacteristic(
                    group_name="Электрические характеристики",
                    field_name="Частота сети",
                    value="50 Гц",
                )
            ]
        )

    monkeypatch.setattr(extraction_module, "run_structured", flaky)
    monkeypatch.setattr(extraction_module, "MAX_CHUNK_CHARS", 100)

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="x" * 250,  # гарантированно больше одного куска
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.chunks_failed == 1
    assert outcome.chunks_processed >= 1
    assert outcome.saved >= 1


def test_same_characteristic_from_overlapping_chunks_does_not_break_transaction(
    db, product, actor, monkeypatch
):
    """Регрессионный тест на реальный баг. Куски текста режутся С ПЕРЕКРЫТИЕМ (`chunk_text`),
    поэтому одна и та же характеристика закономерно приходит из двух соседних кусков. Без
    `flush()` после вставки повторный `select` не видел добавленный объект (он ещё в сессии,
    не в БД), вторая вставка падала на `uq_product_characteristics_field` и роняла ВСЮ
    транзакцию — терялись и остальные характеристики документа. Сломалось бы на первом же
    реальном многостраничном «Описании типа»."""

    monkeypatch.setattr(
        extraction_module,
        "run_structured",
        lambda db_session, **kwargs: ExtractionResult(
            characteristics=[
                ExtractedCharacteristic(
                    group_name="Электрические характеристики",
                    field_name="Частота сети",
                    value="50 Гц",
                ),
                ExtractedCharacteristic(
                    group_name="Электрические характеристики",
                    field_name="Количество фаз",
                    value="3",
                ),
            ]
        ),
    )
    monkeypatch.setattr(extraction_module, "MAX_CHUNK_CHARS", 100)

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="y" * 400,  # несколько кусков, каждый вернёт те же две характеристики
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.chunks_failed == 0
    # обе характеристики сохранены ровно по одному разу, ничего не потеряно
    stored = db.query(ProductCharacteristic).filter_by(product_id=product.id).all()
    assert {c.field_name for c in stored} == {"Частота сети", "Количество фаз"}
    assert len(stored) == 2


def test_extraction_handles_empty_text(db, product, actor, monkeypatch):
    _stub_extraction(monkeypatch, [])
    outcome = extract_characteristics_for_product(
        db,
        product,
        text="   ",
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )
    assert outcome.saved == 0
    assert outcome.chunks_processed == 0


def test_field_name_prefixed_with_group_is_normalised(db, product, actor, monkeypatch):
    """Регрессионный тест на реальную ошибку, найденную живым вызовом YandexGPT: при плоском
    формате списка полей в промпте («группа → поле») модель воспроизводила строку целиком в
    `field_name`, и ВСЕ 13 корректно извлечённых характеристик отбрасывались фильтром по
    справочнику (saved=0). Промпт исправлен (поля сгруппированы), а нормализация оставлена
    как страховка — корректное значение не должно теряться из-за формы названия поля."""

    _stub_extraction(
        monkeypatch,
        [
            ExtractedCharacteristic(
                group_name="Электрические характеристики",
                field_name="Электрические характеристики → Номинальное напряжение",
                value="3х230/400 В",
            ),
            ExtractedCharacteristic(
                group_name="Метрологические характеристики",
                field_name="Метрологические характеристики: Межповерочный интервал",
                value="16 лет",
            ),
        ],
    )

    outcome = extract_characteristics_for_product(
        db,
        product,
        text="...",
        document_source="fgis",
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        actor=actor,
    )

    assert outcome.saved == 2
    assert outcome.skipped_unknown_field == 0
    stored = {c.field_name for c in db.query(ProductCharacteristic).filter_by(product_id=product.id)}
    assert stored == {"Номинальное напряжение", "Межповерочный интервал"}


def test_system_prompt_groups_fields_without_flat_arrow_format():
    """Справочник в промпте не должен перечислять поля плоско как «группа → поле» — именно
    этот формат провоцировал модель склеивать группу с полем (см. тест выше). Проверяется
    только блок справочника: в тексте инструкций такая строка присутствует намеренно, как
    пример того, как делать НЕ надо."""

    prompt = extraction_module._build_system_prompt("fgis")
    catalogue = prompt.split("Справочник допустимых полей:", 1)[1]

    assert "Электрические характеристики → Номинальное напряжение" not in catalogue
    assert '"Электрические характеристики"' in catalogue
    assert "  - Номинальное напряжение" in catalogue


@pytest.mark.parametrize(
    "raw,expected",
    [(1.0, 1.0), (0.5, 0.5), (95, 0.95), (150, 1.0), (-1, 0.0), (None, None)],
)
def test_clamp_confidence_normalises_model_quirks(raw, expected):
    """Модель иногда возвращает confidence как проценты (95) или вне диапазона —
    колонка Numeric(4,3) такое не примет."""

    assert extraction_module._clamp_confidence(raw) == expected


def test_chunk_text_overlaps_to_avoid_losing_values_on_boundary():
    text = "".join(str(i % 10) for i in range(500))
    chunks = chunk_text(text, max_chars=200, overlap=50)

    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # перекрытие: хвост первого куска должен встречаться во втором
    assert chunks[0][-50:] in chunks[1]
    # ничего не потеряно: конкатенация без перекрытий покрывает весь текст
    assert chunks[0][0] == text[0] and text[-1] in chunks[-1]


def test_chunk_text_returns_single_chunk_for_short_text():
    assert chunk_text("короткий текст", max_chars=1000) == ["короткий текст"]
    assert chunk_text("", max_chars=1000) == []


class TestFieldResolution:
    """Модель называет поля своими словами. Живой разбор 62 руководств (06.09.2026) показал
    три повторяющихся мотива, и каждый из них терял верное значение."""

    def test_group_name_prefix_is_stripped(self):
        from app.seed.characteristics_data import resolve_field

        assert resolve_field("Протоколы обмена", "Протокол DLMS/COSEM") == (
            "Протоколы обмена",
            "DLMS/COSEM",
        )
        assert resolve_field("Интерфейсы и связь", "Интерфейс ZigBee") == (
            "Интерфейсы и связь",
            "ZigBee",
        )

    def test_synonym_maps_to_the_reference_field(self):
        from app.seed.characteristics_data import resolve_field

        assert resolve_field("Конструктивные характеристики", "Вид монтажа") == (
            "Конструктивные характеристики",
            "Тип монтажа",
        )
        # Частное вместо общего: в справочнике одно поле на все сотовые каналы.
        assert resolve_field("Интерфейсы и связь", "GSM") == (
            "Интерфейсы и связь",
            "GSM/GPRS/3G/4G",
        )

    def test_known_field_in_the_wrong_group_is_put_back(self):
        """«Максимальный ток» приходил в «Интерфейсы и связь». Значение верное — группа нет."""

        from app.seed.characteristics_data import resolve_field

        assert resolve_field("Интерфейсы и связь", "Максимальный ток") == (
            "Электрические характеристики",
            "Максимальный ток",
        )

    def test_section_heading_is_not_a_field(self):
        """Название группы вместо имени поля — мусор, его по-прежнему отбрасываем."""

        from app.seed.characteristics_data import resolve_field

        assert resolve_field("Протоколы обмена", "Протоколы обмена") is None
        assert resolve_field("Интерфейсы и связь", "Интерфейсы и связь") is None
        assert resolve_field("Прочие характеристики", "") is None

    def test_qualified_field_name_falls_back_to_the_base_field(self):
        """«Класс точности при измерении активной энергии в двух направлениях» — это класс
        точности; приписка не повод терять значение."""

        from app.seed.characteristics_data import resolve_field

        assert resolve_field(
            "Функциональные возможности",
            "Класс точности при измерении активной энергии в двух направлениях",
        ) == ("Электрические характеристики", "Класс точности")

    def test_named_protocol_goes_to_the_catch_all_field(self):
        """Справочник перечисляет распространённые протоколы поимённо, для прочих держит
        отдельное поле — «Протокол обмена Пульсар» попадает туда, а не теряется."""

        from app.seed.characteristics_data import resolve_field

        assert resolve_field("Протоколы обмена", "Протокол обмена Пульсар") == (
            "Протоколы обмена",
            "Прочие протоколы",
        )

    def test_spodes_is_in_the_reference(self):
        """СПОДЭС в Приложении C нет, а в закупках интеллектуальных приборов учёта он
        обязателен по ПП РФ №890 — поле добавлено сверх приложения."""

        from app.seed.characteristics_data import resolve_field

        assert resolve_field("Протоколы обмена", "Поддержка СПОДЭС") == (
            "Протоколы обмена",
            "СПОДЭС",
        )
