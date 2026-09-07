"""Адаптер каталогов на сайтах производителей-конкурентов (раздел 4.3, 5.3 ТЗ).

Задача та же, что у `app/adapters/mirtek_catalog.py`, — обойти каталог электросчётчиков и
снять модели, характеристики и документы, — но сайтов много и разметка у каждого своя.
Поэтому здесь **один движок обхода и декларативные профили сайтов**: всё, что различается,
вынесено в `SiteProfile`, а всё, что одинаково (вежливость, изоляция ошибок, разбор пар
ключ-значение, отбор документов), написано один раз.

**Почему не «универсальный парсер по эвристикам».** Соблазн велик — но `ManufacturerSiteAdapter`
(поиск руководства вслепую) уже показал цену такого подхода: он работает, потому что ищет
ОДИН документ и может ошибаться. Здесь ошибка означает неверную характеристику в каталоге,
по которой потом считается процент соответствия в тендере. Поэтому селекторы для каждого
сайта — проверенные вручную, а не угаданные.

**Что показала разведка живых сайтов (04.09.2026), профиль за профилем:**

*Энергомера* (`energomera.ru`) — самый структурированный из трёх:
- четыре категории электросчётчиков (`single-phase`, `single-phase-multipurpose`,
  `three-phase`, `multipurpose`); `high-voltage` не берём — высоковольтные приборы учёта
  исключены из справочника так же, как у МИРТЕК, а `additional-equipment` вообще не счётчики;
- отдельная категория `archive` — «снятые с серийного производства», 71 карточка, с активными
  категориями **не пересекается**. Это прямой источник статуса «снят с производства», такой
  же по смыслу, как пометка `<i>` у МИРТЕК, только вынесенный в отдельный раздел;
- в листинге карточка — `div.configurator__content__item` с `h2` (название модели) и
  `p.content__item__article` («Артикул: 101001003007791»);
- `robots.txt` закрывает `/documentations/` — ссылки на документы туда сохраняем в справочник,
  но сами файлы не качаем (нам это и не нужно: характеристики берутся со страницы).

*КПЗ* (`kpsz.ru`) — вся продукция на одной странице:
- `www.kpsz.ru` **редиректит** на `kpsz.ru`; без перехода по редиректу страница приходит без
  каталога вовсе — на это легко попасться и решить, что сайт отдаёт пустой список;
- секции размечены `h1` («Однофазные счетчики электрической энергии», «Трехфазные…»,
  «Дисплей потребителя»), модели — `h2`. Категория определяется ближайшим предыдущим `h1`,
  а не URL: отдельных страниц категорий на сайте нет;
- «Дисплей потребителя» отсеивается тем же правилом, что и вся не-счётчиковая продукция, —
  по названию секции.

*Промэнерго* (`promenergo-rt.ru`):
- одна категория `/production/ipu/`, карточки `section#bl-catalogue li a.title`;
- на карточке `h3` «Ключевые особенности» и `h3` «Технические характеристики» — структура
  ближе всего к МИРТЕК.

Общее для всех трёх и для МИРТЕК: сбой одной карточки или категории не прерывает обход
(раздел 5.9 ТЗ), пауза между запросами, User-Agent, повторные попытки — из `http_utils`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.base import PollError
from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify
from app.adapters.mirtek_catalog import (
    CatalogItem,
    CatalogOutcome,
    CatalogProductDetails,
    DocumentLink,
)

REQUEST_TIMEOUT_SECONDS = 30.0
CRAWL_DELAY_SECONDS = 0.7
MAX_FEATURES_CHARS = 20_000

# Ссылка на документ — по расширению файла. Для Энергомеры этого мало: там путь
# `/documentations/product/ce101_re.pdf` расширение имеет, а вот у КПЗ встречаются ссылки
# с обрезанным именем — их ловит тот же шаблон по каталогу загрузок.
_DOCUMENT_URL_RE = re.compile(r"\.(pdf|docx?|zip|rar)(\?|$)|/documentations/|/uploads/.*documents/", re.IGNORECASE)

# Названия разделов и категорий, которые в справочник не идут. Справочник ограничен
# счётчиками электрической энергии (раздел 2.2.1, 2.2.2 ТТ), и «Дисплей потребителя»,
# «Высоковольтные приборы учёта», УСПД сюда не относятся.
# Признаки чужого прибора, при которых позиция отбрасывается всегда: они называют класс
# устройства, а не деталь комплектации. «Высоковольтный прибор учёта» — тоже счётчик по
# названию, но эта категория исключена из справочника наравне с МИРТЕК-135-РУ.
_NON_METER_SECTION_RE = re.compile(
    r"дисплей|высоковольт|усп[дк]|шкаф|щит|поверочн|трансформатор|"
    r"приложени|программ\w*\s+обеспечени|мобильн",
    re.IGNORECASE,
)
# Признаки слабые: «модуль» и «антенна» встречаются в описании комплектации самого счётчика
# («…со сменным модулем i-PROM.1»), поэтому отбрасывают позицию только тогда, когда она и
# на счётчик-то не похожа.
_WEAK_NON_METER_RE = re.compile(
    r"модул|антенн|адаптер|конфигуратор|ретрансл|терминал|координатор|концентратор|шлюз",
    re.IGNORECASE,
)
_METER_SECTION_RE = re.compile(r"счет|счёт|прибор\w*\s+учет|прибор\w*\s+учёт", re.IGNORECASE)

# Значение-описание файла: у Энергомеры список документов свёрстан такой же таблицей пар,
# что и характеристики («Руководство по эксплуатации :: PDF 2 Mb»), и без этой проверки
# названия документов попадали в справочник характеристиками.
_FILE_VALUE_RE = re.compile(r"^(pdf|jpe?g|png|docx?|xlsx?|zip|rar)\b", re.IGNORECASE)

# Заголовочная строка таблицы характеристик. У Энергомеры она размечена обычными `td`
# («Показатели» / «Величины») и без этой проверки становится «характеристикой».
_TABLE_HEADER_RE = re.compile(
    r"^(показател|наименование\s+(?:показател|параметр)|параметр|характеристик)",
    re.IGNORECASE,
)

# Сколько фаз у прибора — по названию категории/секции. Значение идёт в поле «Количество фаз»
# Приложения C: в таблицах характеристик оно есть не у всех производителей, а требованием
# в закупках стоит почти всегда.
_PHASE_RE = (
    (re.compile(r"трех|трёх|3-?фаз", re.IGNORECASE), "3"),
    (re.compile(r"одно|1-?фаз", re.IGNORECASE), "1"),
)


@dataclass(frozen=True)
class CategorySpec:
    """Категория каталога.

    `device_type` попадает в справочник как «Тип прибора» и задаёт количество фаз.
    `discontinued` помечает раздел вроде «снятые с серийного производства» — у Энергомеры
    это отдельная категория, и все её карточки получают соответствующий статус."""

    device_type: str
    url: str
    discontinued: bool = False


@dataclass(frozen=True)
class SiteProfile:
    """Всё, чем один сайт отличается от другого. Селекторы — проверенные вручную на живой
    разметке; см. докстринг модуля."""

    key: str
    manufacturer_legal_name: str
    base_url: str
    categories: tuple[CategorySpec, ...]

    # --- Как найти карточки в листинге ---
    # Контейнер одной позиции. `None` — позиции не обёрнуты общим блоком, и ссылки берутся
    # со всей страницы по `product_path_re` (так устроены КПЗ и Промэнерго).
    item_selector: str | None = None
    # Ссылка на карточку внутри контейнера.
    link_selector: str = "a[href]"
    # Название модели в листинге. `None` — берём текст ссылки.
    name_selector: str | None = None
    article_selector: str | None = None
    # Чем ссылка на карточку товара отличается от прочих ссылок страницы.
    product_path_re: str = r"^/"
    # Категория определяется не URL страницы, а заголовком секции над списком (КПЗ).
    section_heading_selector: str | None = None

    # --- Что забрать с карточки товара ---
    spec_selectors: tuple[str, ...] = ("table",)
    features_selectors: tuple[str, ...] = ()
    # Заголовок, после которого идёт блок «Ключевые особенности» (когда он не выделен классом).
    features_heading_re: str | None = None
    title_selector: str = "h1"
    # Часть заголовка карточки, которую надо срезать, чтобы получить название модели:
    # у Энергомеры H1 — «Счетчик электроэнергии однофазный CE101 R5 145 M6».
    title_prefix_re: str | None = None
    # Обозначение модели внутри наименования. У Промэнерго позиция называется «Однофазный
    # прибор учета электроэнергии i-PROM.1»; наименование сохраняется целиком (так прибор
    # называют в закупке), а код нужен отдельно — по нему модель сопоставляется с
    # обозначением типа в Госреестре.
    model_code_re: str | None = None
    # Пауза между запросами. Значение по умолчанию подходит большинству сайтов, но Тайпит
    # на 0,7 с начинает отвечать 503 — на живом обходе так потерялось 27 карточек из 70.
    crawl_delay: float = CRAWL_DELAY_SECONDS
    # Наименование брать с карточки товара, а не из листинга. У КПЗ в листинге стоит короткое
    # «М2М-1С», а на карточке — полное «Счетчик электроэнергии однофазный компактный M2M-1С».
    prefer_card_title: bool = False
    # Блок-пара «характеристика → значение», размеченный не таблицей: у МИРТЕК это
    # `.productPropsWrap`, у Waviot — `.catalog-card__list__unit`. Внутри блока берутся два
    # первых прямых потомка: первый — название характеристики, второй — значение.
    spec_pair_selector: str = ".productPropsWrap"
    # Параметр разбивки на страницы («PAGEN_1» у Битрикса) и предел числа страниц. По
    # умолчанию — одна: у большинства сайтов каталог счётчиков помещается на страницу целиком,
    # а лишний запрос на несуществующую вторую страницу битрикс отдаёт как копию первой.
    page_param: str | None = None
    max_pages: int = 1


PROFILES: dict[str, SiteProfile] = {
    "energomera": SiteProfile(
        key="energomera",
        manufacturer_legal_name="АО «Энергомера»",
        base_url="https://www.energomera.ru",
        categories=(
            CategorySpec("Однофазный счётчик электроэнергии", "https://www.energomera.ru/ru/products/meters/single-phase"),
            CategorySpec("Однофазный счётчик электроэнергии", "https://www.energomera.ru/ru/products/meters/single-phase-multipurpose"),
            CategorySpec("Трёхфазный счётчик электроэнергии", "https://www.energomera.ru/ru/products/meters/three-phase"),
            CategorySpec("Трёхфазный счётчик электроэнергии", "https://www.energomera.ru/ru/products/meters/multipurpose"),
            # Снятые с производства — отдельным разделом, статус проставляется всем его позициям.
            CategorySpec("Счётчик электроэнергии", "https://www.energomera.ru/ru/products/meters/archive", discontinued=True),
        ),
        item_selector=".configurator__content__item",
        link_selector="a.content__item__link[href]",
        name_selector="h2",
        article_selector=".content__item__article",
        product_path_re=r"^/ru/products/meters/[^/]+$",
        spec_selectors=("table",),
        title_selector="h1",
        # H1 карточки — «Счетчик электроэнергии однофазный CE101 R5 145 M6»: наименование
        # производителя целиком, полнее короткого «CE101 R5 145 M6» из листинга.
        prefer_card_title=True,
        model_code_re=r"(?:CE|СЕ|ЦЭ|СУ|ЦУ|МК|Ф)\s?\d+[A-Za-zА-Яа-я0-9.\s-]*",
    ),
    "kpsz": SiteProfile(
        key="kpsz",
        manufacturer_legal_name="ООО «КПЗ»",
        # Без www: `www.kpsz.ru` редиректит сюда, и запрос без follow_redirects возвращает
        # страницу без каталога (проверено — легко принять за пустой сайт).
        base_url="https://kpsz.ru",
        # Раздел «Продукция», а не «Категория/счетчики»: на втором наименования полнее, но
        # карточки там без характеристик и документов вовсе — только описание. Полное
        # наименование при этом никуда не девается, оно есть на самой карточке товара
        # («Счетчик электроэнергии однофазный компактный M2M-1С»), см. `prefer_card_title`.
        categories=(CategorySpec("Счётчик электроэнергии", "https://kpsz.ru/production/"),),
        item_selector=None,
        name_selector=None,
        product_path_re=r"^/[a-z0-9-]+/?$",
        section_heading_selector="h1",
        spec_selectors=("table",),
        features_selectors=(".sp-tab__collapse",),
        title_selector="h2",
        prefer_card_title=True,
        model_code_re=r"[МM]2[МM][-\s]?\d+[A-ZА-Яa-zа-я]*",
    ),
    "promenergo": SiteProfile(
        key="promenergo",
        manufacturer_legal_name="ООО «ПРОМЭНЕРГО»",
        base_url="https://promenergo-rt.ru",
        categories=(CategorySpec("Счётчик электроэнергии", "https://promenergo-rt.ru/products/ipu/"),),
        item_selector="#bl-catalogue li",
        link_selector="a.title[href]",
        product_path_re=r"^/(products|production)/ipu/[^/]+/?$",
        spec_selectors=("table",),
        features_heading_re=r"ключевые особенности",
        title_selector="h1",
        model_code_re=r"i-?PROM[.\w-]*",
    ),
    "taipit": SiteProfile(
        key="taipit",
        manufacturer_legal_name="ООО «Тайпит»",
        base_url="https://www.meters.taipit.ru",
        categories=(
            CategorySpec("Счётчик электроэнергии", "https://www.meters.taipit.ru/catalog/neva/electro-pp-rf-890/"),
            CategorySpec("Однофазный счётчик электроэнергии", "https://www.meters.taipit.ru/catalog/neva/odnofaznyie-schetchiki/mnogotarifnyie/"),
            CategorySpec("Трёхфазный счётчик электроэнергии", "https://www.meters.taipit.ru/catalog/neva/trehfaznyie-schetchiki/odnotarifnye/"),
            CategorySpec("Трёхфазный счётчик электроэнергии", "https://www.meters.taipit.ru/catalog/neva/trehfaznyie-schetchiki/mnogotarifnyie/"),
            CategorySpec("Счётчик электроэнергии", "https://www.meters.taipit.ru/catalog/neva/snyatye-s-proizvodstva/", discontinued=True),
        ),
        item_selector=".item",
        link_selector="a[href]",
        # Позиции адресуются числовым идентификатором: /catalog/neva/<раздел>/<подраздел>/21/
        product_path_re=r"^/catalog/neva/(?:[a-z0-9-]+/){1,2}\d+/?$",
        # Сайт отвечает 503 при частых запросах: на 0,7 с обход терял 27 карточек из 70.
        crawl_delay=2.5,
        spec_selectors=("table",),
        title_selector="h1",
        # Код модели не выделяется: у Тайпита наименование позиции («НЕВА МТ 113 AS OP
        # 5(100) А») и есть обозначение прибора целиком, а любая попытка вырезать из него
        # «код» рвёт строку на запятой в «НЕВА 303 0,5T0».
    ),
    "nartis": SiteProfile(
        key="nartis",
        manufacturer_legal_name="ООО «Завод Нартис»",
        base_url="https://www.nartis.ru",
        categories=(
            CategorySpec("Счётчик электроэнергии", "https://www.nartis.ru/catalog/pribory-ucheta-elektricheskoy-energii/"),
        ),
        item_selector=".card-item-product",
        link_selector="a[href]",
        product_path_re=r"^/catalog/pribory-ucheta-elektricheskoy-energii/[a-z0-9-]+/?$",
        # Таблицы характеристик на карточке нет вовсе — только описание, документы и ПО.
        # Характеристики для Нартиса берутся AI-разбором описания; отсутствие таблицы здесь
        # штатный исход, а не сбой разбора.
        spec_selectors=("table",),
        title_selector="h1",
        # H1 карточки — короткое «НАРТИС-И100», а в листинге полное «Счётчик однофазный
        # интеллектуальный НАРТИС-И100»: здесь, наоборот, наименование берётся из листинга.
        prefer_card_title=False,
        model_code_re=r"НАРТИС[\u2011\u2013\w.-]*",
    ),
    "waviot": SiteProfile(
        key="waviot",
        # Юрлицо указано на самой карточке товара, рядом с наименованием.
        manufacturer_legal_name="ООО «Телематические Решения»",
        base_url="https://waviot.ru",
        categories=(
            CategorySpec("Счётчик электроэнергии", "https://waviot.ru/catalog/power-meters/"),
        ),
        item_selector=None,
        link_selector="a.catalog__unit__title[href]",
        product_path_re=r"^/catalog/power-meters/[A-Za-z0-9-]+/?$",
        # Таблиц на карточке нет вовсе: характеристики свёрстаны парами блоков внутри
        # секции «Характеристики».
        spec_selectors=(".catalog-card__section",),
        spec_pair_selector=".catalog-card__list__unit",
        features_selectors=(".catalog-card__features",),
        title_selector="h1",
        # Пауза по умолчанию (0,7 с) даёт HTTP 429 — на пробном обходе так потерялись три
        # карточки из пяти; на 1,5 с сайт отвечает ровно, здесь взято с запасом.
        crawl_delay=2.0,
        # Наименование у Waviot короткое с обеих сторон — «ФОБОС 1», «ФОБОС 3 Сплит»; так
        # прибор называет производитель, подзаголовок карточки («Счётчик электрической
        # энергии статический однофазный…») — описание типа, а не название модели.
        model_code_re=r"ФОБОС\s?\d+(?:\s?[А-ЯA-Z](?![а-яa-z]))?",
    ),
    "rim": SiteProfile(
        key="rim",
        manufacturer_legal_name="АО «Радио и Микроэлектроника»",
        base_url="https://www.ao-rim.ru",
        categories=(
            CategorySpec(
                "Счётчик электроэнергии",
                "https://www.ao-rim.ru/product/schyetchiki/klassicheskoe-ispolnenie/",
            ),
            CategorySpec(
                "Счётчик электроэнергии",
                "https://www.ao-rim.ru/product/schyetchiki/split-ispolnenie/",
            ),
        ),
        # Высоковольтные счётчики (третий подраздел) в справочник не идут: раздел 2.2.2 ТЗ
        # ограничивает его приборами учёта прямого включения и трансформаторного включения
        # низкого напряжения.
        item_selector=".catalog-list__info-title",
        link_selector="a[href]",
        product_path_re=r"^/product/schyetchiki/[a-z-]+/[a-z0-9.-]+/?$",
        spec_selectors=("table",),
        title_selector="h1",
        # Наименование — обозначение прибора («РиМ 189.40»), полного названия сайт не даёт
        # ни в листинге, ни на карточке.
        model_code_re=r"РиМ\s?\d+\.\d+[А-ЯA-Z]?(?:-\d+[А-ЯA-Z]?)?",
    ),
    "milur": SiteProfile(
        key="milur",
        manufacturer_legal_name="ООО «МИЛУР Интеллектуальные Системы»",
        base_url="https://miluris.ru",
        categories=(
            CategorySpec(
                "Однофазный счётчик электроэнергии",
                "https://miluris.ru/produktsiya/odnofaznyye-schetchiki-elektrichestva/",
            ),
            CategorySpec(
                "Трёхфазный счётчик электроэнергии",
                "https://miluris.ru/produktsiya/trekhfaznyye-schetchiki-elektrichestva/",
            ),
            CategorySpec(
                "Счётчик электроэнергии",
                "https://miluris.ru/produktsiya/snyatie-s-seriinogo-proizvodstva/",
                discontinued=True,
            ),
        ),
        item_selector=".catalog-block__info-title",
        link_selector="a[href]",
        # Карточка лежит либо в подкатегории («…/odnofaznyye-…-7m/milur-107S-22-RZ-1L-DT/»),
        # либо прямо в разделе — так устроен раздел снятых с производства.
        product_path_re=r"^/produktsiya/[A-Za-z_-]+/(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9._-]+/?$",
        spec_selectors=("table",),
        title_selector="h1",
        # Наименование позиции и есть обозначение прибора целиком, с исполнением:
        # «Милур 107S.22-RZ-1L-DT».
        model_code_re=r"Милур\s?\d+[A-ZА-Я]?[\w.-]*",
        page_param="PAGEN_1",
        max_pages=6,
    ),
    "incotex": SiteProfile(
        key="incotex",
        # Справочник производителей (раздел 4.3 ТЗ) называет юрлицо «НПК Инкотекс»; сайт
        # incotexcom.ru ведёт «Инкотекс-СК» — это одна группа, и связь профиля со
        # справочником идёт по названию из ТЗ.
        manufacturer_legal_name='ООО «НПК "Инкотекс"»',
        base_url="https://www.incotexcom.ru",
        categories=(
            CategorySpec(
                "Однофазный счётчик электроэнергии",
                "https://www.incotexcom.ru/catalogue/odnofaznye-schyotchiki",
            ),
            CategorySpec(
                "Трёхфазный счётчик электроэнергии",
                "https://www.incotexcom.ru/catalogue/tryohfaznye-schyotchiki",
            ),
            CategorySpec(
                "Счётчик электроэнергии",
                "https://www.incotexcom.ru/catalogue/discontinued",
                discontinued=True,
            ),
        ),
        item_selector=None,
        link_selector="a[href]",
        # Карточки лежат на первом уровне раздела и адресуются коротким кодом модели:
        # /catalogue/201-8, /catalogue/204artm. Списки подкатегорий («odnofaznye-odnotarifnye»)
        # такому виду не отвечают — в них есть дефис между буквенными словами, а здесь после
        # цифры.
        product_path_re=r"^/catalogue/\d[A-Za-z0-9.-]*$",
        spec_selectors=("table",),
        title_selector="h1",
        # robots.txt сайта требует Crawl-delay: 10 — соблюдаем, обход одного раздела при
        # четырёх десятках позиций займёт минуты, но это условие владельца сайта.
        crawl_delay=10.0,
        model_code_re=r"Меркурий\s?\d+[\w.-]*",
    ),
    "mir": SiteProfile(
        key="mir",
        manufacturer_legal_name='ООО «НПО "МИР"»',
        base_url="https://mir-omsk.ru",
        categories=(
            CategorySpec(
                "Счётчик электроэнергии",
                "https://mir-omsk.ru/products/equipment/smart-metering-unit/",
            ),
        ),
        item_selector=".catalog-product__item",
        link_selector="a[href]",
        # Карточки лежат вне раздела оборудования: /products/three-phase-mirs04/.
        product_path_re=r"^/products/(?!equipment|solutions)[a-z0-9-]+/?$",
        name_selector=".catalog-product__title",
        spec_selectors=("table",),
        title_selector="h2",
        # H1 карточки набран прописными («СЧЕТЧИК ЭЛЕКТРОЭНЕРГИИ ТРЕХФАЗНЫЙ МИР С-04») —
        # в справочник должно попасть наименование в обычном регистре, оно в H2 и в листинге.
        model_code_re=r"МИР\s?С-\d+",
    ),
    "pulsar": SiteProfile(
        key="pulsar",
        # Приборы «Пульсар» выпускает рязанское НПП; pulsarm.ru — его московский торговый
        # дом. Юрлицо-изготовитель важно для сопоставления с Госреестром: под маркой
        # «Пульсар» там значатся ещё и пожарные извещатели, и расходомеры других юрлиц.
        manufacturer_legal_name='ООО «НПП "ТЕПЛОВОДОХРАН"»',
        base_url="https://pulsarm.ru",
        categories=(
            CategorySpec(
                "Однофазный счётчик электроэнергии",
                "https://pulsarm.ru/products/pribory-ucheta/"
                "schetchiki-elektroenergii-elektroschetchiki/odnofaznye/",
            ),
            CategorySpec(
                "Трёхфазный счётчик электроэнергии",
                "https://pulsarm.ru/products/pribory-ucheta/"
                "schetchiki-elektroenergii-elektroschetchiki/trekhfaznye/",
            ),
        ),
        # Раздел «Комплектующие» не берём, но и в самих разделах счётчиков лежат антенны,
        # ретрансляторы и терминалы — их отсеивает `_is_meter_position`.
        item_selector=".product-prev__content_text",
        link_selector="a[href]",
        # Карточка всегда лежит внутри подкатегории: /…/odnofaznye/elektroschetchik-…/.
        # Ссылки на сами подкатегории сюда не попадают — они в меню, а не в блоке позиции.
        product_path_re=(
            r"^/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/"
            r"[a-z-]+/[a-z0-9-]+/?$"
        ),
        spec_selectors=("table",),
        title_selector="h1",
        # Сайт отвечает 6 секунд на страницу и срывается в таймаут при частых запросах.
        crawl_delay=2.0,
        model_code_re=r"Пульсар\s?\d+(?:\s?[А-ЯA-Z](?![а-яa-z]))?",
        page_param="PAGEN_1",
        max_pages=8,
    ),
    "rotek": SiteProfile(
        key="rotek",
        manufacturer_legal_name="ООО «НТЦ Ротек»",
        base_url="https://rotek.ru",
        categories=(
            CategorySpec(
                "Счётчик электроэнергии",
                "https://rotek.ru/product/smart-pribory-ucheta1/pribory-ucheta-elektroenergii/",
            ),
        ),
        item_selector=None,
        link_selector="a[href]",
        # Карточки лежат на первом уровне (`/product/rotek-rtm-01s/`), а не внутри раздела.
        product_path_re=r"^/product/rotek-[a-z0-9-]+/?$",
        spec_selectors=("table",),
        title_selector="h1",
        model_code_re=r"РТМ-\d+\s?[A-ZА-Я]?\d*",
    ),
}


class ManufacturerCatalogAdapter:
    """Контракт тот же, что у `MirtekCatalogAdapter`: `list_catalog()` → `get_product_details()`,
    ошибки складываются в `errors`, а не прерывают обход."""

    def __init__(self, profile: SiteProfile, *, crawl_delay: float | None = None) -> None:
        self.profile = profile
        self.source_key = profile.key
        self.crawl_delay = profile.crawl_delay if crawl_delay is None else crawl_delay
        self._session: httpx.Client | None = None

    def list_catalog(self) -> CatalogOutcome:
        outcome = CatalogOutcome()
        seen: set[str] = set()

        client = self._session_client()
        for category in self.profile.categories:
            items = []
            for page_url in self._category_pages(category):
                try:
                    html = self._get_html(client, page_url)
                except Exception as exc:  # noqa: BLE001 - изоляция сбоя одной категории
                    if page_url != category.url and _is_missing_page(exc):
                        # Страница за последней: у Милура сайт отвечает на неё 404, а не
                        # копией последней. Это конец списка, а не сбой обхода.
                        break
                    logger.warning(f"Каталог {self.profile.key}: категория {page_url} не загружена: {exc}")
                    outcome.errors.append(PollError(page_url, f"Категория не загружена: {exc}"))
                    break

                try:
                    page_items = parse_category(html, category, profile=self.profile)
                except Exception as exc:  # noqa: BLE001 - изменение вёрстки одной страницы
                    logger.warning(f"Каталог {self.profile.key}: разметка {page_url} не разобрана: {exc}")
                    outcome.errors.append(
                        PollError(page_url, f"Разметка категории не разобрана: {exc}")
                    )
                    break

                known = {item.url for item in items}
                fresh = [item for item in page_items if item.url not in known]
                items.extend(fresh)
                if not fresh:
                    # Страница за последней у Битрикса отдаёт содержимое последней, а не
                    # пустоту: признак конца — что новых позиций на ней нет.
                    break

            if not items:
                # Живая страница без карточек — почти наверняка сменилась вёрстка.
                # Молчаливый ноль заставил бы сервис решить, что продукция исчезла с сайта.
                outcome.errors.append(
                    PollError(
                        category.url,
                        "Страница категории загрузилась, но не содержит ни одной карточки — "
                        "вероятно, изменилась вёрстка сайта",
                    )
                )
            for item in items:
                if item.url in seen:
                    # Одна модель может стоять в двух категориях сайта; в справочнике
                    # это одна запись (ключ — URL карточки).
                    continue
                seen.add(item.url)
                outcome.items.append(item)

        logger.info(
            f"Каталог {self.profile.key}: собрано позиций {len(outcome.items)}, "
            f"ошибок обхода {len(outcome.errors)}"
        )
        return outcome

    def _category_pages(self, category: CategorySpec) -> list[str]:
        """Адреса страниц одной категории. Без `page_param` — единственный адрес категории."""

        if not self.profile.page_param or self.profile.max_pages <= 1:
            return [category.url]

        pages = [category.url]
        parts = urlparse(category.url)
        for number in range(2, self.profile.max_pages + 1):
            query = dict(parse_qsl(parts.query))
            query[self.profile.page_param] = str(number)
            pages.append(urlunparse(parts._replace(query=urlencode(query))))
        return pages

    def get_product_details(self, url: str) -> CatalogProductDetails:
        html = self._get_html(self._session_client(), url)
        return parse_product_card(html, url, profile=self.profile)

    def _session_client(self) -> httpx.Client:
        """Одно соединение на весь обход.

        Не микрооптимизация: TLS-хендшейк к этим сайтам занимает от 0,5 до 2,9 секунды
        (замерено — у КПЗ 2,9 с, у Нартиса 0,5 с), а карточек в каталоге до двух сотен.
        Клиент на каждый запрос означал столько же хендшейков, сколько карточек, — на
        Энергомере это лишние минуты обхода и лишняя нагрузка на чужой сервер."""

        if self._session is None:
            self._session = self._client()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> "ManufacturerCatalogAdapter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            # follow_redirects обязателен: `www.kpsz.ru` редиректит на `kpsz.ru`, и без
            # перехода каталог не приходит вовсе.
            follow_redirects=True,
            verify=resolve_verify(self.profile.base_url),
        )

    def _get_html(self, client: httpx.Client, url: str) -> str:
        if self.crawl_delay:
            time.sleep(self.crawl_delay)
        response = fetch_with_retry(client, "GET", url)
        response.raise_for_status()
        return response.text


def _is_missing_page(exc: Exception) -> bool:
    """404 ли это. Сайты по-разному заканчивают разбивку на страницы: одни отдают копию
    последней страницы, другие — «не найдено»."""

    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 404


# --- Чистые функции разбора: без сети, тестируются на сохранённой разметке ---


def parse_category(html: str, category: CategorySpec, *, profile: SiteProfile) -> list[CatalogItem]:
    """Страница категории → позиции каталога."""

    soup = BeautifulSoup(html, "lxml")
    if profile.section_heading_selector:
        return _parse_sectioned_page(soup, category, profile=profile)
    return _parse_listing(soup, category, profile=profile)


def _parse_listing(soup: BeautifulSoup, category: CategorySpec, *, profile: SiteProfile) -> list[CatalogItem]:
    containers = (
        soup.select(profile.item_selector) if profile.item_selector else [soup]
    )
    product_re = re.compile(profile.product_path_re)
    items: list[CatalogItem] = []
    seen: set[str] = set()

    for container in containers:
        for anchor in container.select(profile.link_selector):
            href = anchor.get("href")
            if not href:
                continue
            absolute = urljoin(category.url, href)
            if not product_re.match(urlparse(absolute).path):
                continue
            # Завершающий слэш сохраняется: у Тайпита адрес карточки без него отвечает 404,
            # и обход терял все 70 позиций разом. Для сравнения с уже виденными URL слэш
            # снимается отдельно — иначе «/21» и «/21/» считались бы разными позициями.
            normalised = absolute.split("#", 1)[0]
            if normalised.rstrip("/") in seen:
                continue

            scope = container if profile.item_selector else anchor
            full_name = _text_of(scope, profile.name_selector) or _clean(anchor.get_text(" ", strip=True))
            if not full_name:
                # Ссылка-картинка без подписи: у Ротека и КПЗ на одну позицию приходится две
                # ссылки, и название несёт вторая. URL в «уже виденные» пока не добавляем —
                # иначе она была бы отброшена как дубль, и позиция потерялась бы целиком.
                continue
            seen.add(normalised.rstrip("/"))
            if not _is_meter_position(full_name):
                # Не-счётчик, затесавшийся в раздел счётчиков: у Энергомеры в категории
                # оказался «Трансформатор тока». Справочник ограничен электросчётчиками
                # (раздел 2.2.2 ТТ), и такая позиция засоряет и каталог, и привязку к
                # типам СИ, для которой она заведомо кандидатов не найдёт.
                continue
            # Запасной артикул — slug карточки. Обрезается по той же границе, что и значение
            # из разметки: у Нартиса slug длиной в семь слов, и без предела он упёрся бы
            # в `products.article` (`String(100)`) на первой же длинной позиции.
            article = (
                _article_from(scope, profile)
                or normalised.rstrip("/").rsplit("/", 1)[-1][:MAX_ARTICLE_LENGTH]
            )

            items.append(
                CatalogItem(
                    # Наименование сохраняется как у производителя: именно в этом виде
                    # прибор называют в закупочной документации.
                    model_name=full_name,
                    model_code=_model_code(full_name, profile),
                    article=article,
                    execution=None,
                    url=normalised,
                    category=category.url,
                    # Категория даёт тип прибора, но не всегда фазность: у Энергомеры раздел
                    # «снятые с производства» смешанный, у Промэнерго категория одна на всё.
                    # Тогда фазность выводится из названия позиции — в закупках она стоит
                    # требованием почти всегда, и терять её нельзя.
                    device_type=_device_type_from_section(full_name, category.device_type),
                    discontinued=category.discontinued,
                    mounting_badge=None,
                )
            )
    return items


def _parse_sectioned_page(
    soup: BeautifulSoup, category: CategorySpec, *, profile: SiteProfile
) -> list[CatalogItem]:
    """Одна страница с секциями вместо отдельных страниц категорий (КПЗ).

    Тип прибора берётся из ближайшего предшествующего заголовка секции, а секции, которые
    счётчиками не являются («Дисплей потребителя»), пропускаются целиком."""

    product_re = re.compile(profile.product_path_re)
    items: list[CatalogItem] = []
    seen: set[str] = set()
    current_section = ""

    heading_tags = {profile.section_heading_selector, profile.title_selector}
    for element in soup.find_all(list(heading_tags | {"a"})):
        if element.name != "a":
            if element.name == profile.section_heading_selector:
                current_section = _clean(element.get_text(" ", strip=True))
            continue

        href = element.get("href")
        if not href:
            continue
        absolute = urljoin(category.url, href)
        if not product_re.match(urlparse(absolute).path):
            continue
        if not _is_meter_section(current_section):
            continue
        normalised = absolute.split("#", 1)[0].rstrip("/")
        if normalised in seen:
            continue

        name = _clean(element.get_text(" ", strip=True))
        if not name:
            # Первая ссылка позиции — картинка без подписи; название придёт со следующей.
            continue
        seen.add(normalised)
        items.append(
            CatalogItem(
                model_name=name,
                model_code=_model_code(name, profile),
                article=normalised.rsplit("/", 1)[-1],
                execution=None,
                url=normalised,
                category=category.url,
                device_type=_device_type_from_section(current_section, category.device_type),
                discontinued=category.discontinued,
                mounting_badge=None,
            )
        )
    return items


def parse_product_card(html: str, url: str, *, profile: SiteProfile) -> CatalogProductDetails:
    """Карточка товара → характеристики, документы, «Ключевые особенности»."""

    soup = BeautifulSoup(html, "lxml")
    details = CatalogProductDetails(url=url)

    heading = soup.select_one(profile.title_selector)
    if heading is not None:
        title = _clean(heading.get_text(" ", strip=True))
        # Заголовок карточки сохраняется целиком: у Энергомеры и КПЗ он и есть полное
        # наименование производителя, а вычленять из него код — задача `model_code`.
        details.model_name = _strip_title_prefix(title, profile) or title
        description = []
        for sibling in heading.find_all_next("p", limit=6):
            text = _clean(sibling.get_text(" ", strip=True))
            if not text:
                continue
            # Абзацы-характеристики («Класс точности: 1») в описание не идут: они и так
            # разбираются как пары ключ-значение, а здесь были бы шумом. Подписи ссылок на
            # документы — тоже: у КПЗ список документов размечен теми же абзацами, и без
            # проверки в «Наименование полное» попадало «Руководство по эксплуатации».
            if ":" in text[:40] or sibling.find("a") is not None:
                continue
            description.append(text)
        if description:
            details.description = "\n".join(description[:3])

    details.specifications = extract_key_value_pairs(
        soup, profile.spec_selectors, pair_selector=profile.spec_pair_selector
    )
    details.features_text = _extract_features(soup, profile)
    if not details.features_text and not details.specifications and details.description:
        # У Нартиса на карточке нет ни таблицы характеристик, ни блока особенностей — только
        # описание, в котором характеристики перечислены прозой («измерений параметров сети:
        # среднеквадратических значений напряжения и силы переменного тока, частоты сети…»).
        # Отдаём его AI-разбору: иначе от такой карточки в справочнике останутся одни
        # документы.
        details.features_text = details.description
    details.documents = _extract_documents(soup, url)
    return details


def extract_key_value_pairs(
    soup: BeautifulSoup,
    selectors: tuple[str, ...],
    *,
    pair_selector: str = ".productPropsWrap",
) -> dict[str, str]:
    """Пары «характеристика → значение» из таблиц и из блоков-пар.

    Универсально по замыслу: берутся любые пары, а не заданный список полей — набор
    характеристик у разных производителей разный и меняется без предупреждения. Отбор и
    маппинг на Приложение C делает сервис, а не адаптер.

    Строки с одинаковыми ячейками (`X :: X`) и с пустым значением пропускаются: на реальных
    страницах так выглядят разделители секций. Заголовочная строка таблицы («Показатели ::
    Величины» у Энергомеры) отсеивается отдельно — она размечена как обычная строка данных,
    и без явной проверки попала бы в справочник характеристикой «Показатели»."""

    pairs: dict[str, str] = {}
    for selector in selectors:
        for block in soup.select(selector):
            for row in block.select("tr"):
                cells = row.find_all(["td", "th"], recursive=False) or row.find_all(["td", "th"])
                if len(cells) != 2:
                    continue
                key = _clean(cells[0].get_text(" ", strip=True))
                value = _clean(cells[1].get_text(" ", strip=True))
                if not key or not value or key == value:
                    continue
                if _TABLE_HEADER_RE.match(key) or all(c.name == "th" for c in cells):
                    continue
                if _FILE_VALUE_RE.match(value):
                    continue
                # Ячейка-«простыня» — это вложенная таблица целиком, а не характеристика.
                if len(key) > 200 or len(value) > 400:
                    continue
                pairs.setdefault(key, value)
            # Разметка парами div: так устроен МИРТЕК (`.productPropsWrap`) и Waviot, у
            # которого таблицы характеристик нет вовсе — только пары блоков.
            for pair in block.select(pair_selector):
                cells = pair.find_all("div", recursive=False)
                if len(cells) < 2:
                    continue
                key = _clean(cells[0].get_text(" ", strip=True))
                value = _clean(cells[1].get_text(" ", strip=True))
                if key and value:
                    pairs.setdefault(key, value)
    return pairs


def _extract_features(soup: BeautifulSoup, profile: SiteProfile) -> str | None:
    """Блок свободного текста («Ключевые особенности», «Особенности») — вход AI-экстракции."""

    blocks: list[str] = []
    for selector in profile.features_selectors:
        for node in soup.select(selector):
            text = _block_text(node)
            if text:
                blocks.append(text)

    if profile.features_heading_re:
        pattern = re.compile(profile.features_heading_re, re.IGNORECASE)
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if not pattern.search(_clean(heading.get_text(" ", strip=True))):
                continue
            collected: list[str] = []
            for sibling in heading.find_next_siblings():
                if sibling.name in ("h2", "h3", "h4"):
                    break  # дошли до следующего раздела
                text = _block_text(sibling)
                if text:
                    collected.append(text)
            if collected:
                blocks.append("\n".join(collected))

    joined = "\n".join(dict.fromkeys(blocks))
    return joined[:MAX_FEATURES_CHARS] or None


# Подпись ссылки, состоящая из одного размера файла: «642.6 КБ», «1.2 MB».
_SIZE_ONLY_RE = re.compile(r"[\d.,]+\s*(?:кб|мб|гб|kb|mb|gb|б|b)", re.IGNORECASE)


def _preceding_label(anchor) -> str | None:
    """Текст, стоящий перед ссылкой, — название документа, когда сама ссылка подписана
    размером файла. Ищется в пределах соседних узлов, а не по всей странице: дальше начнётся
    описание другого документа."""

    for previous in anchor.parent.find_all_previous(string=True, limit=12):
        text = _clean(str(previous))
        if not text or _SIZE_ONLY_RE.fullmatch(text):
            continue
        return text
    return None


def _extract_documents(soup: BeautifulSoup, page_url: str) -> list[DocumentLink]:
    """Ссылки на документы карточки.

    Группы, как у МИРТЕК, здесь нет — на этих сайтах документы перечислены одним списком.
    Поэтому назначение документа определяется по его названию, а сервис уже раскладывает
    их по полям «Документация» Приложения C. Подпись «Скачать» игнорируется: на Промэнерго
    у каждого документа две ссылки — название и кнопка."""

    documents: list[DocumentLink] = []
    seen: set[tuple[str, str]] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not _DOCUMENT_URL_RE.search(href):
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        if _SIZE_ONLY_RE.fullmatch(title or ""):
            # У Инкотекса подписью ссылки служит размер файла («642.6 КБ»), а название
            # документа стоит перед ней отдельным текстом. Без этого в справочник попадали
            # документы с названием «452.7 КБ», и руководство среди них было не найти.
            title = _clean(_preceding_label(anchor)) or title
        if not title or title.lower() in ("скачать", "download", "pdf"):
            continue
        if _SIZE_ONLY_RE.fullmatch(title):
            continue
        title = re.sub(r"\s*\((?:pdf|docx?|zip)[^)]*\)\s*$", "", title, flags=re.IGNORECASE)
        absolute = urljoin(page_url, href)
        key = (title.lower(), absolute)
        if key in seen:
            continue
        seen.add(key)
        documents.append(DocumentLink(group="", title=title, url=absolute))
    return documents


def _is_meter_position(name: str) -> bool:
    """Позиция каталога — счётчик, который должен попасть в справочник?

    Два списка признаков, и разделены они не для красоты. Сильные («высоковольтный»,
    «дисплей», «мобильное приложение») называют класс устройства и перевешивают даже прямое
    «прибор учёта электроэнергии» в названии — так из каталога Нартиса уходят НАРТИС-И500
    (высоковольтный, категория исключена из справочника) и «Нартис ПУЛЬТ» (приложение).
    Слабые («модуль», «антенна») встречаются в описании комплектации самого счётчика
    («…со сменным модулем i-PROM.1») и отбрасывают позицию только тогда, когда она и на
    счётчик не похожа."""

    if _NON_METER_SECTION_RE.search(name):
        return False
    if _METER_SECTION_RE.search(name):
        return True
    return not _WEAK_NON_METER_RE.search(name)


def _is_meter_section(section: str) -> bool:
    if not section:
        return False
    if _NON_METER_SECTION_RE.search(section):
        return False
    return bool(_METER_SECTION_RE.search(section))


def _device_type_from_section(section: str, default: str) -> str:
    for pattern, phases in _PHASE_RE:
        if pattern.search(section):
            return f"{'Трёхфазный' if phases == '3' else 'Однофазный'} счётчик электроэнергии"
    return default


def phases_for_device_type(device_type: str | None) -> str | None:
    """Количество фаз по названию типа прибора. `None` — из названия не следует."""

    for pattern, phases in _PHASE_RE:
        if pattern.search(device_type or ""):
            return phases
    return None


def normalise_phases(value: str | None) -> str | None:
    """Значение поля «Количество фаз» к числу.

    У МИРТЕК фазность выводится из категории и пишется числом, у Энергомеры на карточке
    стоит отдельная характеристика «Фазность» со значением «Однофазный». Одно и то же
    свойство в двух видах несравнимо: требование тендера «трёхфазный» надо сверять с одним
    значением, а не с двумя написаниями."""

    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return text
    return phases_for_device_type(text)


def _model_code(name: str, profile: SiteProfile) -> str | None:
    """Обозначение модели, вычлененное из длинного названия позиции."""

    if not profile.model_code_re:
        return None
    match = re.search(profile.model_code_re, name, re.IGNORECASE)
    return match.group(0).strip(" .,;") if match else None


def _strip_title_prefix(title: str, profile: SiteProfile) -> str | None:
    if not profile.title_prefix_re:
        return None
    stripped = re.sub(profile.title_prefix_re, "", title, flags=re.IGNORECASE).strip()
    return stripped or None


def _text_of(scope, selector: str | None) -> str | None:
    if not selector:
        return None
    node = scope.select_one(selector)
    return _clean(node.get_text(" ", strip=True)) if node is not None else None


# Предел длины артикула. Колонка `products.article` — `String(100)`, и дело не только в ней:
# на архивных карточках Энергомеры селектор артикула подхватывает описание прибора целиком
# («ТУ 4228-027-46146329-2000 Счетчик предназначен для измерения…»). На живом обходе это
# уронило 64 карточки из 220 на `StringDataRightTruncation` — то есть треть каталога
# производителя молча не сохранилась бы, останься проверка только в базе.
MAX_ARTICLE_LENGTH = 64


def _article_from(scope, profile: SiteProfile) -> str | None:
    """Артикул из листинга. У Энергомеры он подписан («Артикул: 101001003007791») —
    подпись убирается, иначе она попадёт в справочник вместе с номером.

    Если под селектором оказался не артикул, а текст (см. `MAX_ARTICLE_LENGTH`), значение
    отбрасывается: вызывающий код возьмёт slug карточки, который артикулом и является."""

    raw = _text_of(scope, profile.article_selector)
    if not raw:
        return None
    article = re.sub(r"^\s*артикул\s*[:№]?\s*", "", raw, flags=re.IGNORECASE).strip()
    if not article or len(article) > MAX_ARTICLE_LENGTH:
        return None
    return article


def _block_text(node) -> str:
    """Текст блока с сохранением построчной структуры — списки и абзацы несут смысл, и
    склейка их в одну строку заметно ухудшает качество AI-экстракции."""

    if getattr(node, "name", None) is None:
        return ""
    lines: list[str] = []
    for element in node.find_all(["p", "li", "h4", "strong"]):
        if element.find(["p", "li"]) is not None:
            continue
        text = _clean(element.get_text(" ", strip=True))
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    if not lines:
        text = _clean(node.get_text(" ", strip=True))
        return text
    return "\n".join(lines)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
