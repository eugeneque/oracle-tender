"""Тесты адаптера каталога МИРТЕК (задача 2 задания).

Фикстуры — сокращённые, но **дословные по разметке** фрагменты живых страниц
`mirtekgroup.com` (разведка 04.09.2026): та же вложенность `div`, те же классы
(`listWrap`, `listBadge`, `productPropsWrap`, `productDocsWrap`, `tabItem3`), та же форма
пометки «Снят с производства» (`<i>` рядом с названием) и те же ссылки-исполнения по
заводам. Придумывать разметку «как удобно парсеру» здесь бессмысленно: адаптер существует
ровно для того, чтобы переживать разметку сайта такой, какая она есть.

Отдельно проверяется главное правило отбора: в справочник идут только российские
исполнения (Таганрог/Владивосток), а «Казахстан» и «Беларусь» — нет.
"""

from __future__ import annotations

import pytest

from app.adapters.mirtek_catalog import (
    CATEGORIES,
    MirtekCatalogAdapter,
    is_russian_execution,
    parse_category,
    parse_product_card,
)

SINGLE_PHASE_URL = CATEGORIES["Однофазный счётчик электроэнергии"]
DEVICE_TYPE = "Однофазный счётчик электроэнергии"


def _card_block(model: str, links: list[tuple[str, str]], *, badge: str, discontinued: bool = False) -> str:
    """Одна ячейка страницы категории — структура повторяет живую разметку."""

    mark = "<i>Снят с производства</i>" if discontinued else ""
    link_html = "".join(
        f'<div><a href="/produkciya/odnofaznye-schyotchiki/{slug}">{label}</a></div>'
        for slug, label in links
    )
    head_slug = links[0][0]
    return f"""
      <div class="odd">
        <div>
          <a href="/produkciya/odnofaznye-schyotchiki/{head_slug}" aria-label="{model}"></a>
          <div>
            <p><span>{model}</span>{mark}</p>
            {link_html}
          </div>
          <div>
            <div class="listAddString">
              <div class="listBadge" style="background-color: #8aabef">{badge}</div>
            </div>
            <div class="imgWrap"><img src="/sites/default/files/x.png" alt="" /></div>
          </div>
        </div>
      </div>
    """


CATEGORY_HTML = f"""
<html><body>
  <div class="headWrap"><h1>Однофазные счётчики электроэнергии</h1></div>
  <div class="listWrap">
    <div class="row">
      {_card_block(
        "МИРТЕК-12-РУ-D17",
        [("mirtek-12-ru-D17", "Таганрог"), ("mirtek-212-ru-d17", "Владивосток")],
        badge="DIN-рейка",
      )}
      {_card_block(
        "МИРТЕК-12-РУ-W2",
        [
            ("mirtek-12-ru-w2", "Таганрог"),
            ("mirtek-212-ru-w2", "Владивосток"),
            ("mirtek-12-kz-w2", "Казахстан"),
            ("mirtek-1-by-w2", "Беларусь"),
        ],
        badge="Щиток",
        discontinued=True,
      )}
      {_card_block(
        "МИРТЕК-1-BY-W6b",
        [("mirtek-1-by-w6b", "Беларусь")],
        badge="Щиток",
      )}
    </div>
  </div>
  <footer><a href="https://t.me/mirtek" aria-label="Telegram">Telegram</a></footer>
</body></html>
"""


PRODUCT_CARD_HTML = """
<html><body>
  <div class="headWrap">
    <div>
      <h1>МИРТЕК-12-РУ-D17</h1>
      <p>Интеллектуальный прибор учёта электроэнергии однофазный для установки на DIN-рейку.</p>
    </div>
  </div>
  <div class="productFeatures">
    <div class="wrapper">
      <div>
        <p><strong>Основные интерфейсы связи:</strong></p>
        <ul><li>Оптопорт;</li><li>RS485.</li></ul>
        <p>Возможность хранить показания на начало суток (за 128 суток).</p>
      </div>
      <div>
        <p>Поддерживаемые протоколы передачи данных: «МИРТЕК», СПОДЭС версии 2, 3.2, 4.</p>
      </div>
    </div>
  </div>
  <div class="infoWrap productInfoWrap">
    <div class="wrapper">
      <div class="tabItem tabItem1">
        <div class="productDocsWrap">
          <div>
            <div>Разрешительные документы</div>
            <div>
              <a href="/download/2724"><span>
                <span><strong>Сертификат</strong> об утверждении и <strong>описание типа</strong> средств измерений МИРТЕК-12-РУ</span>
                <span>(pdf, 7.43 MB)</span>
              </span></a>
              <a href="/download/2728"><span>
                <span><strong>Декларация</strong> соответствия МИРТЕК-12-РУ</span>
                <span>(pdf, 814.34 KB)</span>
              </span></a>
            </div>
          </div>
          <div>
            <div>Руководства</div>
            <div>
              <a href="/download/4739"><span>
                <span><strong>Руководство</strong> по эксплуатации МИРТЕК-12-РУ (D17, SP17)</span>
                <span>(pdf, 2.6 MB)</span>
              </span></a>
            </div>
          </div>
        </div>
      </div>
      <div class="tabItem tabItem3">
        <div class="productPropsWrap">
          <div><p>Класс точности по активной/реактивной энергии</p></div><div><p>1/1</p></div>
        </div>
        <div class="productPropsWrap">
          <div><p>Номинальное напряжение</p></div><div><p>220 В или 230 В</p></div>
        </div>
        <div class="productPropsWrap">
          <div><p>Базовый&nbsp;ток</p></div><div><p>5 А</p></div>
        </div>
        <div class="productPropsWrap">
          <div><p>Срок службы счётчика, не менее</p></div><div><p>48 лет</p></div>
        </div>
        <div class="productPropsWrap">
          <div><p>Совершенно новая характеристика</p></div><div><p>значение</p></div>
        </div>
      </div>
      <div class="tabItem tabItem4">
        <div>
          <div class="productSymbolString"><span>МИРТЕК-12-РУ</span><span>D17</span></div>
          <div class="productSymbolLegend">
            <div><p>Тип счётчика</p></div>
            <div><p>Тип корпуса</p><p>D17 для установки на DIN-рейку</p></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</body></html>
"""


class TestCategoryParsing:
    def test_only_russian_executions_are_collected(self):
        """Главное правило отбора: «Казахстан» и «Беларусь» в справочник не попадают —
        для российских тендеров они нерелевантны (п.2.1 задания)."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        articles = [item.article for item in items]

        assert articles == [
            "mirtek-12-ru-D17",
            "mirtek-212-ru-d17",
            "mirtek-12-ru-w2",
            "mirtek-212-ru-w2",
        ]
        assert not any("-kz-" in a or "-by-" in a for a in articles)

    def test_model_without_russian_executions_is_skipped_entirely(self):
        """МИРТЕК-1-BY-W6b выпускается только в Беларуси — записи каталога быть не должно
        вовсе, а не пустой «головной» карточки."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        assert all("W6b" not in item.model_name for item in items)

    def test_each_russian_execution_becomes_its_own_record(self):
        """Таганрог и Владивосток — отдельные записи с общей моделью и разными артикулами:
        у них различаются характеристики и документы (п.2.1 задания)."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        d17 = [item for item in items if item.model_name == "МИРТЕК-12-РУ-D17"]

        assert len(d17) == 2
        assert {item.execution for item in d17} == {"Таганрог", "Владивосток"}
        assert {item.article for item in d17} == {"mirtek-12-ru-D17", "mirtek-212-ru-d17"}
        assert all(item.url.startswith("https://mirtekgroup.com/produkciya/") for item in d17)

    def test_head_link_and_taganrog_link_are_one_record_not_two(self):
        """«Головная» ссылка карточки и ссылка «Таганрог» ведут на один и тот же URL —
        дубля быть не должно."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        urls = [item.url for item in items]
        assert len(urls) == len(set(urls))

    def test_discontinued_mark_is_captured_for_all_executions(self):
        """Пометка стоит у модели целиком — значит, относится к каждому её исполнению
        (критерий приёмки 4)."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        w2 = [item for item in items if item.model_name == "МИРТЕК-12-РУ-W2"]

        assert len(w2) == 2
        assert all(item.discontinued for item in w2)
        assert all(not item.discontinued for item in items if item.model_name != "МИРТЕК-12-РУ-W2")

    def test_mounting_badge_is_captured(self):
        """Тип монтажа есть только на бейдже страницы категории — на карточке товара такого
        поля нет вовсе."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        badges = {item.model_name: item.mounting_badge for item in items}
        assert badges["МИРТЕК-12-РУ-D17"] == "DIN-рейка"
        assert badges["МИРТЕК-12-РУ-W2"] == "Щиток"

    def test_footer_links_are_not_mistaken_for_products(self):
        """У ссылок на соцсети в подвале тоже есть `aria-label` — опора на него подтянула бы
        Telegram в каталог."""

        items = parse_category(CATEGORY_HTML, SINGLE_PHASE_URL, device_type=DEVICE_TYPE)
        assert all("Telegram" not in item.model_name for item in items)

    def test_broken_markup_yields_empty_list_not_exception(self):
        """Смена вёрстки не должна ронять обход — пустой результат обрабатывается
        вызывающим сервисом как «страница изменилась» (раздел 5.9 ТЗ)."""

        assert parse_category("<html><body><p>Ничего</p></body></html>", SINGLE_PHASE_URL, device_type=DEVICE_TYPE) == []


class TestExecutionFilter:
    @pytest.mark.parametrize(
        "url, label, expected",
        [
            ("https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/mirtek-12-ru-D17", "Таганрог", True),
            ("https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/mirtek-212-ru-d17", "Владивосток", True),
            ("https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/mirtek-12-kz-w2", "Казахстан", False),
            ("https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/mirtek-1-by-w2", "Беларусь", False),
            # Подпись отсутствует, но маркер страны в slug есть — этого достаточно.
            ("https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/mirtek-12-ru-sp3", "", True),
        ],
    )
    def test_country_markers(self, url, label, expected):
        assert is_russian_execution(url, label) is expected


class TestProductCardParsing:
    @pytest.fixture()
    def details(self):
        return parse_product_card(
            PRODUCT_CARD_HTML,
            "https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/mirtek-12-ru-D17",
        )

    def test_specifications_are_parsed_universally(self, details):
        """Парсер берёт любые пары ключ-значение, а не заданный список: у трёхфазных
        счётчиков набор может оказаться шире (п.2.3 задания)."""

        assert details.specifications["Класс точности по активной/реактивной энергии"] == "1/1"
        assert details.specifications["Номинальное напряжение"] == "220 В или 230 В"
        assert details.specifications["Срок службы счётчика, не менее"] == "48 лет"
        # Незнакомый ключ тоже попадает в разбор — отбрасывать его на уровне адаптера нельзя.
        assert details.specifications["Совершенно новая характеристика"] == "значение"

    def test_non_breaking_space_in_key_is_normalised(self, details):
        """На сайте встречается «Базовый&nbsp;ток» — без нормализации ключ не совпал бы
        со словарём синонимов."""

        assert details.specifications["Базовый ток"] == "5 А"

    def test_documents_keep_their_group(self, details):
        """Группа документа — стабильный признак; текст ссылки на разных карточках разный
        («Декларация соответствия» / «Декларация о соответствии»)."""

        by_group = {}
        for document in details.documents:
            by_group.setdefault(document.group, []).append(document.url)

        assert by_group["Разрешительные документы"] == [
            "https://mirtekgroup.com/download/2724",
            "https://mirtekgroup.com/download/2728",
        ]
        assert by_group["Руководства"] == ["https://mirtekgroup.com/download/4739"]

    def test_document_title_drops_file_size_suffix(self, details):
        titles = [document.title for document in details.documents]
        assert "Сертификат об утверждении и описание типа средств измерений МИРТЕК-12-РУ" in titles
        assert all("(pdf," not in title for title in titles)

    def test_features_block_keeps_line_structure(self, details):
        """Списки и абзацы на сайте несут смысл («Основные интерфейсы связи:» и перечень под
        ним) — склейка в одну строку ухудшила бы AI-экстракцию."""

        assert details.features_text is not None
        lines = details.features_text.splitlines()
        assert "Основные интерфейсы связи:" in lines
        assert "RS485." in lines
        assert any("СПОДЭС" in line for line in lines)

    def test_symbol_legend_is_kept_as_text(self, details):
        """Расшифровка кодировки артикула в v1 сохраняется текстом для справки — полный
        разбор структуры обозначения не требуется."""

        assert details.symbol_legend is not None
        assert "Тип корпуса" in details.symbol_legend

    def test_model_and_description(self, details):
        assert details.model_name == "МИРТЕК-12-РУ-D17"
        assert "DIN-рейку" in (details.description or "")


class TestAdapterCrawl:
    def test_failed_category_does_not_cancel_the_other(self, monkeypatch):
        """Сбой одной категории не отменяет вторую (раздел 5.9 ТЗ) — иначе недоступность
        трёхфазного раздела оставила бы справочник и без однофазного."""

        adapter = MirtekCatalogAdapter(crawl_delay=0)
        broken_url = CATEGORIES["Трёхфазный счётчик электроэнергии"]

        def fake_get_html(self, client, url):
            if url == broken_url:
                raise RuntimeError("HTTP 503")
            return CATEGORY_HTML

        monkeypatch.setattr(MirtekCatalogAdapter, "_get_html", fake_get_html)
        outcome = adapter.list_catalog()

        assert outcome.items, "однофазная категория должна быть собрана несмотря на сбой трёхфазной"
        assert len(outcome.errors) == 1
        assert "HTTP 503" in outcome.errors[0].message

    def test_empty_category_is_reported_as_error_not_as_empty_catalog(self, monkeypatch):
        """Живая страница без карточек — почти наверняка смена вёрстки. Молчаливый ноль
        заставил бы сервис синхронизации решить, что вся продукция исчезла с сайта."""

        adapter = MirtekCatalogAdapter(crawl_delay=0)
        monkeypatch.setattr(
            MirtekCatalogAdapter, "_get_html", lambda self, client, url: "<html><body></body></html>"
        )
        outcome = adapter.list_catalog()

        assert outcome.items == []
        assert len(outcome.errors) == len(CATEGORIES)
        assert all("вёрстка" in error.message for error in outcome.errors)
