"""Тесты обучения справочника по Аршину и поиска документации (замечание заказчика
15.09.2026).

Живой пример, вокруг которого всё построено: НАРТИС-И100 — в редакции 2 «Описания типа»
появился корпус W115, которого нет в каталоге на сайте производителя, а руководство на него
находится поиском на официальном сайте. Проверяется каждое звено: разбор исполнений из
карточки типа, заведение исполнения в каталог, разбор «Описания типа» с расшифровкой
условного обозначения, отбор документов из поисковой выдачи, сохранение неизвестных полей.

Сеть не задействована: карточки Аршина и выдача поисковика — слепки живых ответов
15.09.2026, модель подменяется заглушкой.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.adapters.fgis import (
    SiSearchResult,
    execution_designation,
    match_notation_prefix,
    tested_modifications as parse_tested_modifications,
)
from app.adapters.yandex_search import SearchHit, YandexSearchError, parse_search_xml
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ProductDataSource,
    ReviewStatus,
    SiType,
    SiTypeSource,
)
from app.services import (
    characteristic_extraction,
    document_discovery,
    fgis_catalog_sync,
    fgis_description_ingest,
    registry_modifications,
)

# --- Слепок карточки типа 86199-22 (НАРТИС-И100), редакция 2 от 01.06.2026 ---

I100_CARD = {
    "number": "86199-22",
    "title": "Счетчики электроэнергии однофазные интеллектуальные",
    "j_notation": ["НАРТИС-И100"],
    "j_modification": [
        "НАРТИС-И100 – (ХХХХ)2 – (Х)3 – (ХХХХХ)4, где:",
        "НАРТИС-И100 - тип счетчика;",
        "(ХХХХ)2 - тип корпуса;",
        "(ХХХХ)7 - максимальный ток;",
        "На испытания представлены:",
        "НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D",
        "НАРТИС-И100-W112-2-A1R1-230-5-100A-ST-RS485-RF2400/1-P1-HKLMOQ1V3-D",
        "НАРТИС-И100-W113-2-A1R1-230-5-100A-ST-RS485-RF868/1-P1-EHKLMOQ1V3-D",
    ],
    "j_factorynums": (
        '[{"modification":"НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D",'
        '"notation":"НАРТИС-И100","factory_num":"021250559295"},'
        '{"modification":"НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D",'
        '"notation":"НАРТИС-И100","factory_num":"021250559299"}]'
    ),
}

W115 = "НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D"
W112 = "НАРТИС-И100-W112-2-A1R1-230-5-100A-ST-RS485-RF2400/1-P1-HKLMOQ1V3-D"

SEARCH_XML = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0">
<response date="20260914T212516">
<results><grouping attr="" mode="flat">
<group><doc id="1">
<url>https://www.nartis.ru/upload/iblock/5f6/nmge5xsoiskcr0avk6yrwilnnctko9rp.pdf</url>
<domain>www.nartis.ru</domain>
<title>Счетчик электроэнергии однофазный интеллектуальный...</title>
<mime-type>application/pdf</mime-type>
<passages><passage>НРДЛ.411152.101РЭ Настоящее <hlword>руководство</hlword> по эксплуатации счетчика
<hlword>НАРТИС</hlword>-<hlword>И100</hlword> (далее – счетчик).</passage></passages>
</doc></group>
<group><doc id="2">
<url>https://www.nartis.ru/catalog/pribory-ucheta-elektricheskoy-energii/</url>
<domain>www.nartis.ru</domain>
<title>Приборы учёта электрической энергии — каталог продукции</title>
<mime-type>text/html</mime-type>
</doc></group>
<group><doc id="3">
<url>https://manualzz.example/nartis-i100-w115.pdf</url>
<domain>manualzz.example</domain>
<title>НАРТИС-И100-W115 руководство по эксплуатации</title>
<mime-type>application/pdf</mime-type>
</doc></group>
</grouping></results>
</response>
</yandexsearch>"""


class TestTestedModifications:
    def test_registry_card_yields_unique_full_designations(self):
        """`j_factorynums` и легенда дают одни и те же исполнения — дубликаты не плодятся,
        порядок реестра сохраняется."""

        found = parse_tested_modifications(I100_CARD, "НАРТИС-И100")
        assert found == [W115, W112, "НАРТИС-И100-W113-2-A1R1-230-5-100A-ST-RS485-RF868/1-P1-EHKLMOQ1V3-D"]

    def test_several_designations_on_one_line_are_split(self):
        """Реестр набирает часть обозначения латиницей («HAPTИC-P3-C…») и ставит два
        исполнения в одну строку — они должны разойтись по двум записям."""

        card = {
            "j_modification": [
                "(1)НАРТИС-Р3–(2)–(3), где:",
                "(8) - Модификация; на испытания представлены: HAPTИC-P3-C-1010-400-100-RS-BT НАРТИС-Р3-М-1010-400-100-ZB",
            ]
        }
        assert parse_tested_modifications(card, "НАРТИС-Р3") == [
            "HAPTИC-P3-C-1010-400-100-RS-BT",
            "НАРТИС-Р3-М-1010-400-100-ZB",
        ]

    def test_line_without_type_word_is_one_designation(self):
        card = {"j_modification": ["На испытания представлена модификация: УП-04-100-0.2-8-С2"]}
        assert parse_tested_modifications(card, "МИР УП-04") == ["УП-04-100-0.2-8-С2"]

    def test_legend_without_tested_section_gives_nothing(self):
        card = {"j_modification": ["ПУЛЬСАР Х1/Х1 Х2Х3Х4, где: Х1 - тип счетчика"]}
        assert parse_tested_modifications(card, "ПУЛЬСАР") == []


class TestExecutionDesignation:
    @pytest.mark.parametrize(
        ("full", "notation", "expected"),
        [
            (W115, "НАРТИС-И100", "НАРТИС-И100-W115"),
            ("HAPTИC-P3-C-1010-400-100-RS-BT", "НАРТИС-Р3", "НАРТИС-Р3-C"),
            ("Милур 109.1-32-RZ-1-DT", "Милур 109", "Милур 109.1"),
            ("ПУЛЬСАР G16Т СМАРТ-К-У", "ПУЛЬСАР", "ПУЛЬСАР G16Т"),
            ("МИРТЕК-12-РУ-D17-A1R1", "МИРТЕК-12-РУ", "МИРТЕК-12-РУ-D17"),
        ],
    )
    def test_type_plus_first_segment(self, full, notation, expected):
        assert execution_designation(full, notation) == expected

    def test_designation_not_starting_with_type_is_skipped(self):
        """Опечатка реестра («Pl» вместо «Р1») не должна породить мусорную запись каталога."""

        assert execution_designation("HAPTИC-Pl-C-1010-230-100-RS-BT", "НАРТИС-Р1") is None
        assert match_notation_prefix("УП-04-100", "МИР УП-04") is None


@pytest.fixture()
def manufacturer(db_session) -> Manufacturer:
    item = Manufacturer(
        legal_name=f"ООО «Завод Тест {uuid.uuid4().hex[:6]}»",
        brand_name="Тест",
        website="https://nartis-region.ru/counters",
        is_mirtek=False,
    )
    db_session.add(item)
    db_session.commit()
    return item


def _si_type(db_session, manufacturer, **extra) -> SiType:
    fields = dict(
        manufacturer_id=manufacturer.id,
        si_code=f"86199-{uuid.uuid4().hex[:2]}",
        notation="НАРТИС-И100",
        type_name="Счетчики электроэнергии однофазные интеллектуальные",
        mit_uuid="uuid-i100",
        source=SiTypeSource.AUTO_SEARCH.value,
        description_type_version="2",
        allowed_modifications="НАРТИС-И100 – (ХХХХ)2 – …, где: (ХХХХ)2 - тип корпуса; (ХХХХ)7 - максимальный ток",
        tested_modifications=[W115, W112],
    )
    fields.update(extra)
    si_type = SiType(**fields)
    db_session.add(si_type)
    db_session.commit()
    return si_type


def _product(db_session, manufacturer, model_code: str, **extra) -> Product:
    product = Product(
        manufacturer_id=manufacturer.id,
        model_name=f"Счётчик однофазный интеллектуальный {model_code}",
        model_code=model_code,
        data_source=ProductDataSource.MANUFACTURER_SITE.value,
        **extra,
    )
    db_session.add(product)
    db_session.commit()
    return product


class TestDiscoverModifications:
    def test_missing_execution_is_created_from_registry(self, db_session, manufacturer):
        """W115 есть в реестре, но не на сайте — заводится из реестра с пометкой на проверку;
        W112 на сайте есть (с неразрывным дефисом в коде) — не дублируется, но получает полное
        обозначение из реестра."""

        si_type = _si_type(db_session, manufacturer)
        existing = _product(db_session, manufacturer, "НАРТИС‑И100-W112", si_type_id=si_type.id)

        outcome = registry_modifications.discover_modifications(db_session, manufacturer)

        assert outcome.products_created == 1
        assert outcome.created_names == ["НАРТИС-И100-W115"]
        assert outcome.already_known == 1
        created = db_session.scalar(select(Product).where(Product.model_code == "НАРТИС-И100-W115"))
        assert created is not None
        assert created.si_type_id == si_type.id
        assert created.data_source == ProductDataSource.FGIS.value
        assert created.registry_modification == W115
        assert created.review_status == ReviewStatus.NEEDS_REVIEW.value
        assert "на сайте производителя исполнение не найдено" in (created.review_reason or "")
        db_session.refresh(existing)
        assert existing.registry_modification == W112

    def test_repeat_run_is_idempotent(self, db_session, manufacturer):
        _si_type(db_session, manufacturer)
        first = registry_modifications.discover_modifications(db_session, manufacturer)
        second = registry_modifications.discover_modifications(db_session, manufacturer)
        assert first.products_created == 2
        assert second.products_created == 0
        assert second.already_known == 2

    def test_family_record_does_not_swallow_execution(self, db_session, manufacturer):
        """Запись семейства «НАРТИС-И100» — не то же самое, что исполнение W115: исполнение
        заводится отдельно, а семейство остаётся как есть."""

        _si_type(db_session, manufacturer, tested_modifications=[W115])
        _product(db_session, manufacturer, "НАРТИС‑И100")
        outcome = registry_modifications.discover_modifications(db_session, manufacturer)
        assert outcome.products_created == 1

    def test_unlinked_site_product_gets_its_type(self, db_session, manufacturer):
        si_type = _si_type(db_session, manufacturer, tested_modifications=[W112])
        product = _product(db_session, manufacturer, "НАРТИС-И100-W112")
        outcome = registry_modifications.discover_modifications(db_session, manufacturer)
        db_session.refresh(product)
        assert outcome.products_linked == 1
        assert product.si_type_id == si_type.id


class _StubFgis:
    """Адаптер ФГИС без сети: карточка с исполнениями и текст «Описания типа»."""

    def __init__(self, *, text: str | None = "Текст описания типа", version: str = "2", modifications=None):
        self.text = text
        self.version = version
        self.modifications = modifications if modifications is not None else [W115, W112]
        self.fetched: list[str] = []

    def enrich_from_card(self, result: SiSearchResult) -> SiSearchResult:
        result.description_type_version = self.version
        result.tested_modifications = list(self.modifications)
        result.allowed_modifications = "легенда"
        return result

    def fetch_description_type_text(self, url: str, *, mirror_url=None):
        self.fetched.append(url)
        return self.text


def _fake_reading(monkeypatch, *, seen: list[str]):
    """Модель подменяется: запоминает тексты и отдаёт по одной характеристике на текст.
    Из легенды с обозначением «возвращает» максимальный ток исполнения — как живая модель."""

    from app.services.characteristic_extraction import DocumentReading, ExtractedCharacteristic

    def fake(db, *, text, document_source):
        seen.append(text)
        if "Условное обозначение конкретного исполнения" in text:
            current = "80 А" if "80A" in text else "100 А"
            return DocumentReading(
                characteristics=[
                    ExtractedCharacteristic(
                        group_name="Электрические характеристики", field_name="Максимальный ток", value=current
                    )
                ],
                chunks_processed=1,
            )
        return DocumentReading(
            characteristics=[
                ExtractedCharacteristic(
                    group_name="Электрические характеристики", field_name="Максимальный ток", value="60; 80; 100 А"
                ),
                ExtractedCharacteristic(
                    group_name="Электрические характеристики", field_name="Номинальное напряжение", value="230 В"
                ),
            ],
            chunks_processed=1,
        )

    monkeypatch.setattr(characteristic_extraction, "read_characteristics", fake)


def _value(db_session, product: Product, field_name: str) -> str | None:
    return db_session.scalar(
        select(ProductCharacteristic.value).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.field_name == field_name,
        )
    )


class TestDescriptionIngest:
    def test_document_is_read_once_and_executions_are_decoded(self, db_session, manufacturer, monkeypatch):
        """Одно «Описание типа» на семейство: документ вычитывается один раз, характеристики
        получают все привязанные модели, а исполнение из реестра дополнительно расшифровано
        по условному обозначению — его максимальный ток 80 А, а не «60; 80; 100 А» семейства."""

        seen: list[str] = []
        _fake_reading(monkeypatch, seen=seen)
        si_type = _si_type(db_session, manufacturer, description_type_url="https://fgis.example/doc", mit_uuid="u")
        family = _product(db_session, manufacturer, "НАРТИС-И100", si_type_id=si_type.id)
        w115 = _product(
            db_session, manufacturer, "НАРТИС-И100-W115", si_type_id=si_type.id, registry_modification=W115
        )
        adapter = _StubFgis()

        outcome = fgis_description_ingest.ingest_description_types(db_session, manufacturer, adapter=adapter)

        assert adapter.fetched == ["https://fgis.example/doc"]
        assert outcome.documents_read == 1
        assert outcome.products_updated == 2
        assert outcome.modifications_decoded == 1
        assert sum("Условное обозначение конкретного исполнения" not in t for t in seen) == 1
        assert _value(db_session, family, "Максимальный ток") == "60; 80; 100 А"
        assert _value(db_session, w115, "Максимальный ток") == "80 А"
        assert _value(db_session, w115, "Номинальное напряжение") == "230 В"
        # Факты карточки — без обращения к модели.
        assert _value(db_session, w115, "Номер в Госреестре") == si_type.si_code
        db_session.refresh(si_type)
        assert si_type.description_type_extracted_version == "2"
        assert si_type.description_type_text == "Текст описания типа"

    def test_up_to_date_type_is_not_reread(self, db_session, manufacturer, monkeypatch):
        seen: list[str] = []
        _fake_reading(monkeypatch, seen=seen)
        si_type = _si_type(
            db_session, manufacturer, description_type_url="https://fgis.example/doc",
            description_type_text="текст", description_type_extracted_version="2",
        )
        product = _product(db_session, manufacturer, "НАРТИС-И100", si_type_id=si_type.id)
        db_session.add(
            ProductCharacteristic(
                product_id=product.id, group_name="Электрические характеристики",
                field_name="Номинальное напряжение", value="230 В",
                source=CharacteristicSource.FGIS_DESCRIPTION_TYPE.value,
            )
        )
        db_session.commit()

        outcome = fgis_description_ingest.ingest_description_types(db_session, manufacturer, adapter=_StubFgis())
        assert outcome.documents_read == 0
        assert outcome.skipped_up_to_date == 1
        assert seen == []

    def test_new_edition_forces_reread(self, db_session, manufacturer, monkeypatch):
        """Редакция документа изменилась (ревалидация сбросила текст) — документ перечитывается,
        характеристики прежней редакции перезаписываются, подтверждённые человеком — нет."""

        seen: list[str] = []
        _fake_reading(monkeypatch, seen=seen)
        si_type = _si_type(
            db_session, manufacturer, description_type_url="https://fgis.example/doc",
            description_type_version="2", description_type_extracted_version="1", description_type_text=None,
        )
        product = _product(db_session, manufacturer, "НАРТИС-И100", si_type_id=si_type.id)
        db_session.add_all(
            [
                ProductCharacteristic(
                    product_id=product.id, group_name="Электрические характеристики",
                    field_name="Номинальное напряжение", value="220 В (старая редакция)",
                    source=CharacteristicSource.FGIS_DESCRIPTION_TYPE.value,
                ),
                ProductCharacteristic(
                    product_id=product.id, group_name="Электрические характеристики",
                    field_name="Максимальный ток", value="100 А (подтверждено)",
                    source=CharacteristicSource.FGIS_DESCRIPTION_TYPE.value, verified_by_user=True,
                ),
            ]
        )
        db_session.commit()

        outcome = fgis_description_ingest.ingest_description_types(db_session, manufacturer, adapter=_StubFgis())
        assert outcome.documents_read == 1
        assert _value(db_session, product, "Номинальное напряжение") == "230 В"
        assert _value(db_session, product, "Максимальный ток") == "100 А (подтверждено)"

    def test_limit_counts_documents_not_products(self, db_session, manufacturer, monkeypatch):
        seen: list[str] = []
        _fake_reading(monkeypatch, seen=seen)
        for index in range(3):
            si_type = _si_type(db_session, manufacturer, description_type_url=f"https://fgis.example/{index}", notation=f"ТИП-{index}")
            _product(db_session, manufacturer, f"ТИП-{index}-A", si_type_id=si_type.id)
            _product(db_session, manufacturer, f"ТИП-{index}-B", si_type_id=si_type.id)
        outcome = fgis_description_ingest.ingest_description_types(db_session, manufacturer, adapter=_StubFgis(), limit=2)
        assert outcome.documents_read == 2
        assert outcome.products_updated == 4


class TestRevalidationNoticesChanges:
    def test_new_execution_in_registry_is_reported_and_created(self, db_session, manufacturer):
        """Ревалидация видит в карточке исполнение, которого раньше не было, — это и есть
        «изменение в описании типа»: оно попадает в отчёт задачи, а исполнение — в каталог."""

        si_type = _si_type(db_session, manufacturer, tested_modifications=[W112], description_type_version="1", description_type_text="старый")
        _product(db_session, manufacturer, "НАРТИС-И100-W112", si_type_id=si_type.id)

        outcome = fgis_catalog_sync._revalidate(db_session, si_type, adapter=_StubFgis(version="2"))
        db_session.refresh(si_type)

        assert "новые исполнения в реестре" in outcome.message
        assert "W115" in outcome.message
        assert "заведено исполнений: НАРТИС-И100-W115" in outcome.message
        assert si_type.description_type_version == "2"
        assert si_type.description_type_text is None
        assert si_type.description_type_changed_at is not None
        assert db_session.scalar(select(Product).where(Product.model_code == "НАРТИС-И100-W115")) is not None

    def test_unchanged_card_reports_no_changes(self, db_session, manufacturer):
        si_type = _si_type(db_session, manufacturer, tested_modifications=[W115, W112], description_type_version="2")
        outcome = fgis_catalog_sync._revalidate(db_session, si_type, adapter=_StubFgis(version="2"))
        assert "изменений нет" in outcome.message
        db_session.refresh(si_type)
        assert si_type.description_type_changed_at is None


class TestSearchParsing:
    def test_hits_are_parsed_with_passages(self):
        hits = parse_search_xml(SEARCH_XML)
        assert [h.position for h in hits] == [1, 2, 3]
        assert hits[0].is_pdf and hits[0].host == "www.nartis.ru"
        assert "руководство" in hits[0].text and "НАРТИС-И100" in hits[0].text

    def test_nothing_found_is_empty_not_error(self):
        xml = '<?xml version="1.0"?><yandexsearch><response><error code="15">Искомая комбинация слов нигде не встречается</error></response></yandexsearch>'
        assert parse_search_xml(xml) == []

    def test_other_error_is_raised(self):
        xml = '<?xml version="1.0"?><yandexsearch><response><error code="42">Лимит запросов исчерпан</error></response></yandexsearch>'
        with pytest.raises(YandexSearchError):
            parse_search_xml(xml)


class TestDocumentDiscovery:
    def test_official_site_pdf_wins_over_aggregator(self, db_session, manufacturer, monkeypatch):
        """Агрегатор документации упоминает модель дословно, но он не официальный сайт —
        отсекается. Побеждает PDF с сайта производителя, найденный по семейству."""

        from app.adapters import manufacturer_catalog

        monkeypatch.setattr(
            manufacturer_catalog,
            "PROFILES",
            {"test": type("P", (), {"manufacturer_legal_name": manufacturer.legal_name, "base_url": "https://www.nartis.ru"})()},
        )
        si_type = _si_type(db_session, manufacturer)
        product = _product(db_session, manufacturer, "НАРТИС-И100-W115", si_type_id=si_type.id, registry_modification=W115)
        queries: list[str] = []

        def fake_search(db, query, **kwargs):
            queries.append(query)
            return parse_search_xml(SEARCH_XML)

        assert document_discovery.official_domains(db_session, manufacturer) == {"nartis-region.ru", "nartis.ru"}
        found = document_discovery.find_manual_for_product(db_session, product, manufacturer, search=fake_search)

        assert found is not None
        assert found.url.endswith("nmge5xsoiskcr0avk6yrwilnnctko9rp.pdf")
        assert "PDF" in found.reasons and "упоминает семейство" in found.reasons
        assert all(q.startswith("site:") for q in queries)
        assert any("nartis.ru НАРТИС-И100-W115" in q for q in queries)

    def test_discover_saves_link_with_web_search_source(self, db_session, manufacturer):
        manufacturer.website = "https://www.nartis.ru"
        db_session.commit()
        si_type = _si_type(db_session, manufacturer)
        product = _product(db_session, manufacturer, "НАРТИС-И100-W115", si_type_id=si_type.id)
        with_link = _product(db_session, manufacturer, "НАРТИС-И100-W112", si_type_id=si_type.id)
        document_discovery.save_manual_link(db_session, with_link, "https://nartis.ru/old.pdf")

        outcome = document_discovery.discover_documents(
            db_session, manufacturer, search=lambda db, q, **kw: parse_search_xml(SEARCH_XML)
        )

        assert outcome.found == 1
        assert outcome.skipped_have_link == 1
        link = db_session.scalar(
            select(ProductCharacteristic).where(
                ProductCharacteristic.product_id == product.id,
                ProductCharacteristic.field_name == "Ссылка на руководство",
            )
        )
        assert link is not None
        assert link.source == CharacteristicSource.WEB_SEARCH.value
        assert link.value.startswith("https://www.nartis.ru/")

    def test_no_search_results_is_not_found(self, db_session, manufacturer):
        product = _product(db_session, manufacturer, "НАРТИС-И100-W115")
        outcome = document_discovery.DiscoveryOutcome()
        found = document_discovery.find_manual_for_product(
            db_session, product, manufacturer, outcome=outcome, search=lambda db, q, **kw: []
        )
        assert found is None
        assert outcome.queries >= 1

    def test_non_pdf_catalog_page_scores_low(self):
        hit = SearchHit(url="https://www.nartis.ru/catalog/", title="Каталог продукции", domain="www.nartis.ru", mime_type="text/html")
        product = Product(manufacturer_id=uuid.uuid4(), model_name="X", model_code="НАРТИС-И100-W115")
        candidate = document_discovery.score_hit(hit, product=product, si_type=None, domains={"nartis.ru"})
        assert candidate is None or candidate.score < document_discovery.MIN_SCORE


class TestUnknownFieldsArePreserved:
    def test_unknown_field_lands_in_extra_specifications(self, db_session, manufacturer):
        """Поле вне Приложения C больше не выбрасывается: значение остаётся у модели и
        видно в сводке кандидатов на расширение справочника."""

        product = _product(db_session, manufacturer, "НАРТИС-И100-W115")
        outcome = characteristic_extraction.ExtractionOutcome()
        characteristic_extraction.apply_characteristics(
            db_session,
            product,
            [
                characteristic_extraction.ExtractedCharacteristic(
                    group_name="Функциональные возможности", field_name="Поддерживаемые протоколы", value="P1"
                ),
                characteristic_extraction.ExtractedCharacteristic(
                    group_name="Электрические характеристики", field_name="Номинальное напряжение", value="230 В"
                ),
            ],
            characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
            outcome=outcome,
        )
        db_session.commit()
        db_session.refresh(product)

        assert outcome.saved == 1
        assert outcome.skipped_unknown_field == 1
        assert product.extra_specifications == {"Поддерживаемые протоколы": "P1"}
        summary = characteristic_extraction.unknown_fields_summary(db_session, manufacturer.id)
        assert summary == [{"field_name": "Поддерживаемые протоколы", "products": 1, "sample": "Поддерживаемые протоколы: P1"}]


class TestLearningQueue:
    def test_pass_goes_through_catalog_queue(self, db_session, manufacturer, monkeypatch):
        """Полный проход ставится в общую очередь справочника и выполняется её обработчиком;
        итог всех шагов попадает в сообщение задачи, заведённые исполнения — в детали."""

        from app.models.catalog_queue import CatalogQueueStatus
        from app.services import catalog_learning, catalog_queue_service

        catalog_learning.register()
        monkeypatch.setattr(catalog_learning, "refresh_cards", lambda db, m, **kw: 0)
        monkeypatch.setattr(
            fgis_description_ingest, "ingest_description_types",
            lambda db, m, **kw: fgis_description_ingest.DescriptionIngestOutcome(documents_read=1, products_updated=2, characteristics_saved=5),
        )
        monkeypatch.setattr(
            document_discovery, "discover_documents",
            lambda db, m, **kw: document_discovery.DiscoveryOutcome(products_checked=1, found=1),
        )
        from app.services import product_manual_ingest

        monkeypatch.setattr(
            product_manual_ingest, "ingest_manuals",
            lambda db, m, **kw: product_manual_ingest.IngestOutcome(processed=1, characteristics_saved=3),
        )
        _si_type(db_session, manufacturer, tested_modifications=[W115])

        task = catalog_learning.enqueue(db_session, manufacturer, run_now=False)
        assert task is not None and task.status == CatalogQueueStatus.QUEUED.value
        assert catalog_learning.enqueue(db_session, manufacturer, run_now=False).id == task.id

        status = catalog_queue_service.run_task(db_session, task)
        db_session.refresh(task)

        assert status == CatalogQueueStatus.NEEDS_REVIEW.value  # заведено исполнение — человеку смотреть
        assert "НАРТИС-И100-W115" in task.details["created_from_registry"]
        assert "характеристик 5" in task.message and "руководств разобрано 1" in task.message
