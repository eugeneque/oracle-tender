"""Тесты адаптера каталогов конкурентов (Энергомера, КПЗ, Промэнерго).

Фикстуры воспроизводят разметку живых сайтов, разобранную вручную 04.09.2026: те же классы
(`configurator__content__item`, `content__item__article`, `bl-catalogue`), та же структура
секций у КПЗ (категория — заголовком `h1`, модель — `h2`) и те же неудобные особенности,
из-за которых профили выглядят именно так, — заголовочная строка таблицы у Энергомеры и
свёрстанный такой же таблицей список документов.
"""

from __future__ import annotations

import pytest

from app.adapters.manufacturer_catalog import (
    PROFILES,
    CategorySpec,
    ManufacturerCatalogAdapter,
    parse_category,
    parse_product_card,
    phases_for_device_type,
)

ENERGOMERA = PROFILES["energomera"]
KPSZ = PROFILES["kpsz"]
PROMENERGO = PROFILES["promenergo"]
TAIPIT = PROFILES["taipit"]
NARTIS = PROFILES["nartis"]
ROTEK = PROFILES["rotek"]


ENERGOMERA_LISTING = """
<html><body>
  <h1>Счетчики электроэнергии однофазные однотарифные</h1>
  <div class="configurator__content__item meterBox_3" data-product="CE101 R5">
    <a class="content__item__link" href="/ru/products/meters/ce101-r5-145-m6">
      <h2>CE101 R5 145 M6</h2>
      <div class="content__item__list">
        <p class="content__item__param">Класс точности <b>1</b></p>
        <p class="content__item__article">Артикул: 101001003007791</p>
      </div>
    </a>
  </div>
  <div class="configurator__content__item meterBox_1">
    <a class="content__item__link" href="/ru/products/meters/ce101-s6-145">
      <h2>CE101 S6 145</h2>
      <div class="content__item__list">
        <p class="content__item__article">Артикул: 101001003009470</p>
      </div>
    </a>
  </div>
  <div class="configurator__content__item">
    <a class="content__item__link" href="/ru/products/meters/tt">
      <h2>Трансформатор тока</h2>
    </a>
  </div>
  <a href="/ru/products/meters/three-phase">Трехфазные однотарифные</a>
  <a href="/ru/products/askue/about">АСКУЭ</a>
</body></html>
"""

ENERGOMERA_CARD = """
<html><body>
  <h1>Счетчик электроэнергии однофазный CE101 R5 145 M6</h1>
  <p>ТУ 4228-054-22136119-2005 Однофазный электросчетчик серии «СЕ». Устанавливается на din-рейку.</p>
  <table>
    <tr><td>Показатели</td><td>Величины</td></tr>
    <tr><td>Фазность</td><td>Однофазный</td></tr>
    <tr><td>Класс точности</td><td>1</td></tr>
    <tr><td>Номинальное напряжение</td><td>230 В</td></tr>
    <tr><td>Базовый (максимальный) ток</td><td>5 (60) А</td></tr>
    <tr><td>Способ крепления</td><td>DIN-рейка</td></tr>
  </table>
  <table>
    <tr><td>Схема включения</td><td>JPG 101 Kb</td></tr>
    <tr><td>Руководство по эксплуатации</td><td>PDF 2 Mb</td></tr>
  </table>
  <a href="/documentations/product/ce101_re.pdf">Руководство по эксплуатации</a>
  <a href="/documentations/product/ce101_ot.pdf">Описание типа</a>
  <a href="/documentations/product/ce101_ds.pdf">Декларация о соответствии ЕАЭС</a>
</body></html>
"""

KPSZ_LISTING = """
<html><body>
  <h1>Однофазные счетчики электрической энергии</h1>
  <a href="http://www.kpsz.ru/m2m1c/"><img src="/x.png"></a>
  <h2>М2М-1С</h2>
  <a href="http://www.kpsz.ru/m2m1c/">М2М-1С</a>
  <a href="http://www.kpsz.ru/m2m1/"><img src="/y.png"></a>
  <h2>M2M-1</h2>
  <a href="http://www.kpsz.ru/m2m1/">M2M-1</a>
  <h1>Трехфазные счетчики электрической энергии</h1>
  <a href="http://www.kpsz.ru/m2m3/"><img src="/z.png"></a>
  <h2>M2M-3</h2>
  <a href="http://www.kpsz.ru/m2m3/">M2M-3</a>
  <h1>Дисплей потребителя</h1>
  <a href="http://www.kpsz.ru/display-m2m">Дисплей потребителя М2М</a>
  <a href="http://www.kpsz.ru/kontakty/">Контакты</a>
</body></html>
"""

PROMENERGO_LISTING = """
<html><body>
  <h1>Интеллектуальные приборы учёта электроэнергии</h1>
  <section id="bl-catalogue"><div class="holder"><ul>
    <li>
      <div class="photo"><a href="/production/ipu/odnofazniy_pribor_ucheta_elektroenergii_i_prom/"><img src="/a.png"></a></div>
      <a href="/production/ipu/odnofazniy_pribor_ucheta_elektroenergii_i_prom/" class="title">Однофазный прибор учета электроэнергии i-PROM.1</a>
    </li>
    <li>
      <div class="photo"><a href="/production/ipu/trehfazny_pribor_ucheta_elektroenergii_i_prom/"><img src="/b.png"></a></div>
      <a href="/production/ipu/trehfazny_pribor_ucheta_elektroenergii_i_prom/" class="title">Трехфазный прибор учета электроэнергии i-PROM.3</a>
    </li>
  </ul></div></section>
</body></html>
"""

PROMENERGO_CARD = """
<html><body>
  <h1>Однофазный прибор учета электроэнергии i-PROM.1</h1>
  <div class="text">
    <p>Интеллектуальный прибор учёта электроэнергии однофазный многофункциональный.</p>
    <h3>Ключевые особенности</h3>
    <ul><li>Модульная конструкция — оперативная замена модема</li><li>Полное соответствие ПП РФ № 890</li></ul>
    <h3>Технические характеристики</h3>
    <table>
      <tr><td>Установка</td><td>DIN-рейка переходная пластина</td></tr>
      <tr><td>Номинальное фазное напряжение, В</td><td>230</td></tr>
      <tr><td>Базовый ток, А</td><td>5</td></tr>
      <tr><td>Класс точности</td><td>1∕2</td></tr>
    </table>
  </div>
  <a href="/uploads/products/documents/DS i_PROM.1.pdf">Декларация о соответствии ЕАЭС</a>
  <a href="/uploads/products/documents/DS i_PROM.1.pdf">Скачать</a>
</body></html>
"""


class TestEnergomera:
    def test_listing_takes_model_and_article(self):
        """Название модели и артикул лежат в самой карточке листинга — на карточке товара
        H1 длиннее («Счетчик электроэнергии однофазный CE101 R5 145 M6»)."""

        items = parse_category(ENERGOMERA_LISTING, ENERGOMERA.categories[0], profile=ENERGOMERA)

        assert [i.model_name for i in items] == ["CE101 R5 145 M6", "CE101 S6 145"]
        assert items[0].article == "101001003007791"
        assert items[0].device_type == "Однофазный счётчик электроэнергии"

    def test_category_and_foreign_links_are_not_products(self):
        """Ссылки на соседние категории и на АСКУЭ лежат на той же странице и подходят под
        общий шаблон `/ru/products/…` — от них спасает только точный `product_path_re`."""

        items = parse_category(ENERGOMERA_LISTING, ENERGOMERA.categories[0], profile=ENERGOMERA)
        assert all("askue" not in i.url and "three-phase" not in i.url for i in items)

    def test_archive_category_marks_discontinued(self):
        """«Снятые с серийного производства» вынесены отдельным разделом — это и есть
        источник статуса, аналог пометки `<i>` у МИРТЕК."""

        archive = [c for c in ENERGOMERA.categories if c.discontinued]
        assert archive, "в профиле должен быть раздел снятых с производства"

        items = parse_category(ENERGOMERA_LISTING, archive[0], profile=ENERGOMERA)
        assert items and all(i.discontinued for i in items)

    def test_non_meter_position_is_skipped(self):
        """В разделе счётчиков Энергомеры попадаются позиции вроде «Трансформатор тока» —
        справочник ограничен электросчётчиками, и такая запись засоряет и каталог, и
        привязку к типам СИ, для которой кандидатов у неё заведомо нет."""

        items = parse_category(ENERGOMERA_LISTING, ENERGOMERA.categories[0], profile=ENERGOMERA)
        assert all("Трансформатор" not in i.model_name for i in items)

    def test_card_keeps_full_manufacturer_title(self):
        """H1 карточки — наименование производителя целиком, и оно сохраняется как есть:
        в закупочной документации прибор называют именно так, а не кодом модели."""

        details = parse_product_card(ENERGOMERA_CARD, "https://x/a", profile=ENERGOMERA)
        assert details.model_name == "Счетчик электроэнергии однофазный CE101 R5 145 M6"

    def test_table_header_row_is_not_a_characteristic(self):
        """Строка «Показатели :: Величины» размечена как обычная строка данных и без явной
        проверки становится характеристикой «Показатели»."""

        details = parse_product_card(ENERGOMERA_CARD, "https://x/a", profile=ENERGOMERA)
        assert "Показатели" not in details.specifications
        assert details.specifications["Фазность"] == "Однофазный"

    def test_document_table_is_not_a_characteristic(self):
        """Список документов свёрстан такой же таблицей пар — «Руководство по эксплуатации ::
        PDF 2 Mb» попадало в справочник характеристикой."""

        details = parse_product_card(ENERGOMERA_CARD, "https://x/a", profile=ENERGOMERA)
        assert "Руководство по эксплуатации" not in details.specifications
        assert "Схема включения" not in details.specifications

    def test_documents_are_collected(self):
        details = parse_product_card(ENERGOMERA_CARD, "https://x/a", profile=ENERGOMERA)
        titles = [d.title for d in details.documents]
        assert "Описание типа" in titles
        assert "Декларация о соответствии ЕАЭС" in titles


class TestKpsz:
    def test_sections_define_device_type(self):
        """Отдельных страниц категорий на сайте нет — тип прибора берётся из заголовка секции."""

        items = parse_category(KPSZ_LISTING, KPSZ.categories[0], profile=KPSZ)
        by_model = {i.model_name: i.device_type for i in items}

        assert by_model["М2М-1С"] == "Однофазный счётчик электроэнергии"
        assert by_model["M2M-3"] == "Трёхфазный счётчик электроэнергии"

    def test_non_meter_section_is_skipped(self):
        """«Дисплей потребителя» — не счётчик, и в справочник, ограниченный
        электросчётчиками, не идёт."""

        items = parse_category(KPSZ_LISTING, KPSZ.categories[0], profile=KPSZ)
        assert all("Дисплей" not in i.model_name for i in items)

    def test_navigation_links_are_not_products(self):
        items = parse_category(KPSZ_LISTING, KPSZ.categories[0], profile=KPSZ)
        assert all("kontakty" not in i.url for i in items)

    def test_image_link_does_not_create_empty_record(self):
        """У каждой позиции две ссылки на один URL: картинка без подписи и название.
        Записей должно быть столько же, сколько моделей."""

        items = parse_category(KPSZ_LISTING, KPSZ.categories[0], profile=KPSZ)
        assert len(items) == 3
        assert len({i.url for i in items}) == 3
        assert all(i.model_name for i in items)


class TestPromenergo:
    def test_full_name_is_kept_and_code_extracted_alongside(self):
        """Наименование сохраняется как у производителя, а код модели — рядом, отдельным
        полем: по нему прибор сопоставляется с обозначением типа в Госреестре, внутри
        полного наименования оно префиксным сравнением не находится."""

        items = parse_category(PROMENERGO_LISTING, PROMENERGO.categories[0], profile=PROMENERGO)

        assert [i.model_name for i in items] == [
            "Однофазный прибор учета электроэнергии i-PROM.1",
            "Трехфазный прибор учета электроэнергии i-PROM.3",
        ]
        assert [i.model_code for i in items] == ["i-PROM.1", "i-PROM.3"]

    def test_phases_are_inferred_from_position_name(self):
        """Категория на сайте одна на все приборы, поэтому фазность выводится из названия —
        в закупках она стоит требованием почти всегда."""

        items = parse_category(PROMENERGO_LISTING, PROMENERGO.categories[0], profile=PROMENERGO)
        assert items[0].device_type == "Однофазный счётчик электроэнергии"
        assert items[1].device_type == "Трёхфазный счётчик электроэнергии"

    def test_features_block_is_read_by_heading(self):
        """Блок «Ключевые особенности» не выделен классом — он опознаётся по заголовку
        и читается до следующего заголовка."""

        details = parse_product_card(PROMENERGO_CARD, "https://x/a", profile=PROMENERGO)

        assert details.features_text is not None
        assert "Модульная конструкция" in details.features_text
        # Следующий раздел («Технические характеристики») в особенности попасть не должен.
        assert "Номинальное фазное напряжение" not in details.features_text

    def test_download_button_is_not_a_separate_document(self):
        """У каждого документа две ссылки: название и кнопка «Скачать» на тот же файл."""

        details = parse_product_card(PROMENERGO_CARD, "https://x/a", profile=PROMENERGO)
        assert [d.title for d in details.documents] == ["Декларация о соответствии ЕАЭС"]


class TestPhaseHelper:
    @pytest.mark.parametrize(
        "device_type, expected",
        [
            ("Однофазный счётчик электроэнергии", "1"),
            ("Трёхфазный счётчик электроэнергии", "3"),
            ("Трехфазный счетчик электроэнергии", "3"),
            ("Счётчик электроэнергии", None),
            (None, None),
        ],
    )
    def test_phases_for_device_type(self, device_type, expected):
        assert phases_for_device_type(device_type) == expected


class TestCrawlIsolation:
    def test_failed_category_does_not_cancel_the_others(self, monkeypatch):
        """Сбой одной категории не отменяет остальные (раздел 5.9 ТЗ)."""

        adapter = ManufacturerCatalogAdapter(ENERGOMERA, crawl_delay=0)
        broken = ENERGOMERA.categories[1].url

        def fake(self, client, url):
            if url == broken:
                raise RuntimeError("HTTP 503")
            return ENERGOMERA_LISTING

        monkeypatch.setattr(ManufacturerCatalogAdapter, "_get_html", fake)
        outcome = adapter.list_catalog()

        assert outcome.items, "остальные категории должны быть собраны"
        assert any("HTTP 503" in e.message for e in outcome.errors)

    def test_same_model_in_two_categories_is_one_record(self, monkeypatch):
        """Одна позиция может стоять в двух разделах сайта; в справочнике это одна запись
        (ключ — URL карточки)."""

        adapter = ManufacturerCatalogAdapter(ENERGOMERA, crawl_delay=0)
        monkeypatch.setattr(
            ManufacturerCatalogAdapter, "_get_html", lambda self, client, url: ENERGOMERA_LISTING
        )
        outcome = adapter.list_catalog()

        assert len({i.url for i in outcome.items}) == len(outcome.items)

    def test_empty_page_is_reported_as_error(self, monkeypatch):
        """Живая страница без карточек — почти наверняка смена вёрстки. Молчаливый ноль
        заставил бы сервис решить, что вся продукция исчезла с сайта."""

        adapter = ManufacturerCatalogAdapter(PROMENERGO, crawl_delay=0)
        monkeypatch.setattr(
            ManufacturerCatalogAdapter, "_get_html", lambda self, client, url: "<html></html>"
        )
        outcome = adapter.list_catalog()

        assert outcome.items == []
        assert all("вёрстка" in e.message for e in outcome.errors)


class TestProfilesAreConsistent:
    def test_every_profile_targets_a_known_manufacturer(self, db_session):
        """Профиль без строки производителя в справочнике бесполезен: обход некуда сохранять.
        Тест ловит опечатку в юридическом названии — оно связывает профиль со справочником."""

        from sqlalchemy import select

        from app.models.manufacturer import Manufacturer

        known = {m.legal_name for m in db_session.scalars(select(Manufacturer))}
        for key, profile in PROFILES.items():
            assert profile.manufacturer_legal_name in known, (
                f"профиль «{key}» ссылается на «{profile.manufacturer_legal_name}», "
                "которого нет в справочнике производителей"
            )

    def test_categories_are_absolute_urls_on_the_same_host(self):
        from urllib.parse import urlparse

        for key, profile in PROFILES.items():
            host = urlparse(profile.base_url).netloc
            for category in profile.categories:
                assert urlparse(category.url).netloc == host, f"{key}: {category.url}"


TAIPIT_LISTING = """
<html><body>
  <h1>Однофазные многотарифные счетчики</h1>
  <div class="item"><a href="/catalog/neva/odnofaznyie-schetchiki/mnogotarifnyie/21/">НЕВА МТ 113 AS OP 5(100) А</a></div>
  <div class="item"><a href="/catalog/neva/odnofaznyie-schetchiki/mnogotarifnyie/3222/">НЕВА 303 0,5T0 230V/5(10) А</a></div>
  <a href="/catalog/neva/trehfaznyie-schetchiki/odnotarifnye/">Трёхфазные однотарифные</a>
</body></html>
"""

NARTIS_LISTING = """
<html><body>
  <h1>Приборы учёта электрической энергии</h1>
  <div class="card-item-product">
    <a href="/catalog/pribory-ucheta-elektricheskoy-energii/schyetchik-nartis-r1-m/">
      <h3>Счётчик электрической энергии однофазный интеллектуальный НАРТИС‑Р1-М</h3>
    </a>
  </div>
  <div class="card-item-product">
    <a href="/catalog/pribory-ucheta-elektricheskoy-energii/schyetchik-nartis-r3-m/">
      <h3>Счётчик электрической энергии трехфазный интеллектуальный НАРТИС‑Р3-М</h3>
    </a>
  </div>
  <a href="/catalog/pribory-ucheta-vody/">Приборы учёта воды</a>
</body></html>
"""

NARTIS_CARD = """
<html><body>
  <h1>НАРТИС-И100</h1>
  <p>Предназначен для измерения активной и реактивной электроэнергии прямого и обратного
     направления, измерений параметров сети: среднеквадратических значений напряжения и силы
     переменного тока, частоты сети.</p>
  <a href="/upload/re-nartis-i100.pdf">Руководство по эксплуатации НАРТИС-И100</a>
</body></html>
"""

ROTEK_LISTING = """
<html><body>
  <h1>Приборы учета электроэнергии РОТЕК</h1>
  <ul>
    <li><a href="https://rotek.ru/product/rotek-rtm-01s/">РОТЕК РТМ-01 С1 (Сплит)</a></li>
    <li><a href="https://rotek.ru/product/rotek-rtm-03d-v/">РОТЕК РТМ-03 D1 (B1)</a></li>
  </ul>
  <a href="/product/smart-pribory-ucheta1/">Smart приборы учета</a>
  <a href="https://rotek.ru/product/MeterConfig_distr_2_19_6.zip">Скачать дистрибутив</a>
</body></html>
"""


class TestTaipit:
    def test_positions_keep_manufacturer_naming(self):
        """Наименование позиции у Тайпита и есть обозначение прибора целиком — оно и идёт
        в справочник, без попыток вырезать из него «код»."""

        items = parse_category(TAIPIT_LISTING, TAIPIT.categories[1], profile=TAIPIT)

        assert [i.model_name for i in items] == [
            "НЕВА МТ 113 AS OP 5(100) А",
            "НЕВА 303 0,5T0 230V/5(10) А",
        ]
        # Код не выделяется намеренно: любая попытка порвёт «НЕВА 303 0,5T0» на запятой.
        assert all(i.model_code is None for i in items)

    def test_numeric_ids_are_articles(self):
        items = parse_category(TAIPIT_LISTING, TAIPIT.categories[1], profile=TAIPIT)
        assert [i.article for i in items] == ["21", "3222"]

    def test_neighbour_category_link_is_not_a_product(self):
        items = parse_category(TAIPIT_LISTING, TAIPIT.categories[1], profile=TAIPIT)
        assert all("trehfaznyie" not in i.url for i in items)

    def test_archive_category_is_marked(self):
        archive = [c for c in TAIPIT.categories if c.discontinued]
        assert archive, "у Тайпита должен быть раздел снятых с производства"
        items = parse_category(TAIPIT_LISTING, archive[0], profile=TAIPIT)
        assert items and all(i.discontinued for i in items)


class TestNartis:
    def test_full_name_comes_from_listing(self):
        """H1 карточки у Нартиса короткий («НАРТИС-И100»), а полное наименование — в
        листинге. Профиль поэтому не берёт заголовок карточки."""

        items = parse_category(NARTIS_LISTING, NARTIS.categories[0], profile=NARTIS)

        assert items[0].model_name == (
            "Счётчик электрической энергии однофазный интеллектуальный НАРТИС‑Р1-М"
        )
        assert items[0].model_code == "НАРТИС‑Р1-М"
        assert NARTIS.prefer_card_title is False

    def test_phases_inferred_from_name(self):
        items = parse_category(NARTIS_LISTING, NARTIS.categories[0], profile=NARTIS)
        assert items[0].device_type == "Однофазный счётчик электроэнергии"
        assert items[1].device_type == "Трёхфазный счётчик электроэнергии"

    def test_other_catalog_sections_are_not_products(self):
        items = parse_category(NARTIS_LISTING, NARTIS.categories[0], profile=NARTIS)
        assert all("vody" not in i.url for i in items)

    def test_description_becomes_features_when_no_spec_table(self):
        """На карточке нет ни таблицы характеристик, ни блока особенностей — только описание,
        в котором характеристики перечислены прозой. Без этого от карточки в справочнике
        остались бы одни документы."""

        details = parse_product_card(NARTIS_CARD, "https://x/a", profile=NARTIS)

        assert details.specifications == {}
        assert details.features_text and "частоты сети" in details.features_text
        assert len(details.documents) == 1


class TestRotek:
    def test_cards_live_outside_the_section_path(self):
        """Ссылки на карточки — абсолютные на rotek.ru (без www) и лежат на первом уровне,
        а не внутри раздела: `product_path_re` написан под это."""

        items = parse_category(ROTEK_LISTING, ROTEK.categories[0], profile=ROTEK)

        assert [i.model_name for i in items] == ["РОТЕК РТМ-01 С1 (Сплит)", "РОТЕК РТМ-03 D1 (B1)"]
        assert [i.model_code for i in items] == ["РТМ-01 С1", "РТМ-03 D1"]

    def test_section_and_file_links_are_not_products(self):
        items = parse_category(ROTEK_LISTING, ROTEK.categories[0], profile=ROTEK)
        assert all("smart-pribory" not in i.url and ".zip" not in i.url for i in items)


class TestPositionFiltering:
    def test_module_in_description_does_not_reject_a_meter(self):
        """«Однофазный интеллектуальный прибор учёта … со сменным модулем i-PROM.1» —
        это счётчик, а не модуль связи. Проверка «похоже на счётчик» должна идти раньше
        проверки «похоже на чужой прибор», иначе позиция отбрасывалась по слову «модулем»."""

        listing = """
        <html><body><section id="bl-catalogue"><ul><li>
          <a href="/products/ipu/i_prom1_s_split/" class="title">Однофазный интеллектуальный
             прибор учета электрической энергии со сменным модулем i-PROM.1 в корпусе S-SPLIT</a>
        </li></ul></section></body></html>
        """
        items = parse_category(listing, PROMENERGO.categories[0], profile=PROMENERGO)

        assert len(items) == 1
        assert items[0].model_code == "i-PROM.1"


# --- Третья волна: Waviot, РиМ, Милур, Инкотекс, МИР, Пульсар (разведка 05.09.2026) ---

WAVIOT = PROFILES["waviot"]
RIM = PROFILES["rim"]
MILUR = PROFILES["milur"]
INCOTEX = PROFILES["incotex"]
MIR = PROFILES["mir"]
PULSAR = PROFILES["pulsar"]


WAVIOT_LISTING = """
<html><body>
  <a class="catalog__unit__image" href="/catalog/power-meters/phobos-1/"><img src="/x.png"></a>
  <a class="catalog__unit__title" href="/catalog/power-meters/phobos-1/">ФОБОС 1</a>
  <a class="catalog__unit__title" href="/catalog/power-meters/phobos-1S/">ФОБОС 1 Сплит</a>
  <a class="breadcrumbs__unit" href="/catalog/power-meters/">Счетчики электрической энергии</a>
</body></html>
"""

WAVIOT_CARD = """
<html><body>
  <div class="catalog-card__content">
    <h1 class="catalog-card__title">ФОБОС 1</h1>
    <div class="catalog-card__subtitle">Счётчик электрической энергии статический однофазный</div>
    <div class="catalog-card__features">Разработан для установки на объектах жилого назначения</div>
  </div>
  <div class="catalog-card__section">
    <div class="catalog-card__list">
      <div class="catalog-card__list__unit">
        <div class="catalog-card__list__unit__title">Класс точности</div>
        <div class="catalog-card__list__unit__text">Активная энергия: 1</div>
      </div>
      <div class="catalog-card__list__unit">
        <div class="catalog-card__list__unit__title">Базовый (максимальный) ток</div>
        <div class="catalog-card__list__unit__text">5 (80) A</div>
      </div>
    </div>
  </div>
</body></html>
"""

PULSAR_LISTING = """
<html><body>
  <div class="product-prev">
    <div class="product-prev__content_text">
      <a href="/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/odnofaznye/elektroschetchik-pulsar-1t-kompakt-rs-485/">
        Электросчетчик «Пульсар 1Т» Компакт, RS-485, оптопорт, СПОДЭС, 5/100А
      </a>
    </div>
  </div>
  <div class="product-prev">
    <div class="product-prev__content_text">
      <a href="/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/odnofaznye/antenna-antivandalnaya-433-mgts/">
        Антенна антивандальная 433 МГц ИРФ-DTLD433-1М-SMA-80
      </a>
    </div>
  </div>
  <div class="product-prev">
    <div class="product-prev__content_text">
      <a href="/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/odnofaznye/retranslyator-irf-341/">
        Ретранслятор ИРФ-341 (уличное исполнение с креплением на опору)
      </a>
    </div>
  </div>
  <a class="submenu" href="/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/odnofaznye/mnogotarifnye/">Многотарифные</a>
</body></html>
"""

MIR_LISTING = """
<html><body>
  <div class="catalog-product__item">
    <a href="/products/three-phase-mirs04/"><div class="catalog-product__img"></div></a>
    <p class="catalog-product__title">Счетчик электроэнергии трехфазный МИР С-04</p>
    <a href="/products/three-phase-mirs04/">Подробнее</a>
  </div>
  <a href="/products/equipment/controller/">Контроллеры</a>
</body></html>
"""

MIR_CARD = """
<html><body>
  <h1>СЧЕТЧИК ЭЛЕКТРОЭНЕРГИИ ТРЕХФАЗНЫЙ МИР С-04</h1>
  <h2>Счетчик электроэнергии трехфазный МИР С-04</h2>
  <table>
    <tr><td>Наименование параметра</td><td>Значение</td></tr>
    <tr><td>Класс точности при измерении активной энергии</td><td>1/1</td></tr>
  </table>
</body></html>
"""

MILUR_LISTING = """
<html><body>
  <div class="catalog-block__info-title">
    <a href="/produktsiya/odnofaznyye-schetchiki-elektrichestva/odnofaznyye-schetchiki-elektrichestva-7m/milur-107S-22-RZ-1L-DT/">Милур 107S.22-RZ-1L-DT</a>
  </div>
  <a href="/produktsiya/uspd/">УСПД</a>
</body></html>
"""

RIM_LISTING = """
<html><body>
  <div class="catalog-list__info-title"><a href="/product/schyetchiki/klassicheskoe-ispolnenie/rim-489-23-38/">РиМ 489.2Х-3Х</a></div>
  <a href="/product/schyetchiki/vysokovoltnye/">Высоковольтные</a>
</body></html>
"""

INCOTEX_LISTING = """
<html><body>
  <a class="product-intro__slider" href="/catalogue/201-8">Меркурий 201.8</a>
  <a class="subcategories__item" href="/catalogue/odnofaznye-odnotarifnye">Однофазные однотарифные</a>
  <a class="subcategories__item" href="/catalogue/uspd-koncentratory-shlyuzy">УСПД, концентраторы, шлюзы</a>
</body></html>
"""


class TestWaviot:
    def test_specifications_come_from_block_pairs(self):
        """Таблицы характеристик на карточке нет вовсе — только пары блоков."""

        details = parse_product_card(
            WAVIOT_CARD, "https://waviot.ru/catalog/power-meters/phobos-1/", profile=WAVIOT
        )

        assert details.specifications["Класс точности"] == "Активная энергия: 1"
        assert details.specifications["Базовый (максимальный) ток"] == "5 (80) A"

    def test_split_execution_does_not_leak_into_model_code(self):
        """«ФОБОС 1 Сплит» — это ФОБОС 1 в сплит-исполнении, а не модель «ФОБОС 1 С»."""

        items = parse_category(WAVIOT_LISTING, WAVIOT.categories[0], profile=WAVIOT)
        by_name = {item.model_name: item for item in items}

        assert by_name["ФОБОС 1 Сплит"].model_code == "ФОБОС 1"
        assert by_name["ФОБОС 1"].model_code == "ФОБОС 1"

    def test_image_link_does_not_create_a_duplicate(self):
        items = parse_category(WAVIOT_LISTING, WAVIOT.categories[0], profile=WAVIOT)

        assert len({item.url for item in items}) == len(items) == 2


class TestPulsar:
    def test_accessories_in_the_meters_section_are_skipped(self):
        """В разделах счётчиков лежат ещё антенны, ретрансляторы и терминалы."""

        items = parse_category(PULSAR_LISTING, PULSAR.categories[0], profile=PULSAR)

        assert [item.model_code for item in items] == ["Пульсар 1Т"]

    def test_full_manufacturer_name_is_kept(self):
        items = parse_category(PULSAR_LISTING, PULSAR.categories[0], profile=PULSAR)

        assert items[0].model_name.startswith("Электросчетчик «Пульсар 1Т» Компакт")


class TestMir:
    def test_name_is_taken_in_normal_case(self):
        """H1 карточки набран прописными — в справочник должно попасть наименование из H2."""

        details = parse_product_card(
            MIR_CARD, "https://mir-omsk.ru/products/three-phase-mirs04/", profile=MIR
        )

        assert details.model_name == "Счетчик электроэнергии трехфазный МИР С-04"

    def test_table_header_row_is_not_a_characteristic(self):
        details = parse_product_card(
            MIR_CARD, "https://mir-omsk.ru/products/three-phase-mirs04/", profile=MIR
        )

        assert "Наименование параметра" not in details.specifications

    def test_equipment_sections_are_not_products(self):
        items = parse_category(MIR_LISTING, MIR.categories[0], profile=MIR)

        assert [item.url for item in items] == ["https://mir-omsk.ru/products/three-phase-mirs04/"]
        assert items[0].model_code == "МИР С-04"


class TestMilurRimIncotex:
    def test_milur_execution_is_part_of_the_model_code(self):
        """Обозначение прибора у Милура включает исполнение целиком."""

        items = parse_category(MILUR_LISTING, MILUR.categories[0], profile=MILUR)

        assert [item.model_code for item in items] == ["Милур 107S.22-RZ-1L-DT"]

    def test_rim_designation_keeps_its_letter_suffix(self):
        """«РиМ 489.2Х-3Х» без буквенных суффиксов не находится в Госреестре."""

        items = parse_category(RIM_LISTING, RIM.categories[0], profile=RIM)

        assert [item.model_code for item in items] == ["РиМ 489.2Х-3Х"]

    def test_incotex_subcategory_links_are_not_products(self):
        """Карточка адресуется кодом модели (/catalogue/201-8), подкатегория — словами."""

        items = parse_category(INCOTEX_LISTING, INCOTEX.categories[0], profile=INCOTEX)

        assert [item.model_name for item in items] == ["Меркурий 201.8"]


class TestPagination:
    def test_pages_are_requested_until_nothing_new_appears(self, monkeypatch):
        """Битрикс на странице за последней отдаёт содержимое последней, а не пустоту:
        признак конца — что новых позиций на ней нет."""

        adapter = ManufacturerCatalogAdapter(MILUR, crawl_delay=0)
        requested: list[str] = []

        def fake(self, client, url):
            requested.append(url)
            return MILUR_LISTING

        monkeypatch.setattr(ManufacturerCatalogAdapter, "_get_html", fake)
        outcome = adapter.list_catalog()

        assert len(outcome.items) == 1
        # Первая страница каждой категории плюс ровно одна следующая — на ней повтор.
        assert len(requested) == 2 * len(MILUR.categories)
        assert "PAGEN_1=2" in requested[1]

    def test_profile_without_pagination_asks_for_one_page(self, monkeypatch):
        adapter = ManufacturerCatalogAdapter(WAVIOT, crawl_delay=0)
        requested: list[str] = []
        monkeypatch.setattr(
            ManufacturerCatalogAdapter,
            "_get_html",
            lambda self, client, url: requested.append(url) or WAVIOT_LISTING,
        )
        adapter.list_catalog()

        assert requested == [WAVIOT.categories[0].url]
