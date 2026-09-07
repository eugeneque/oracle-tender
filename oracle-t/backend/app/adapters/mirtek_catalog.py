"""Адаптер каталога продукции МИРТЕК на сайте производителя (`mirtekgroup.com`).

**Почему отдельный адаптер, а не `manufacturer_site`.** `ManufacturerSiteAdapter` решает
другую задачу — «найти на незнакомом сайте один документ», поэтому он обходит сайт вслепую
вширь и опирается на эвристики. Здесь сайт известен и разобран вручную (разведка 04.09.2026),
и нужен не документ, а **весь каталог** с характеристиками, документами и статусами. Слепой
обход дал бы на порядок больше запросов к чужому сайту и при этом терял бы структуру.

**Что показала разведка живого сайта** (всё ниже проверено на реальной разметке, а не
предположено):

- Каталог трёхуровневый: страница продукции → категория → карточка товара.
- В парсинг берутся только две категории — однофазные и трёхфазные счётчики электроэнергии.
  Остальные разделы (вода, тепло, газ, высоковольтные приборы, ПО, щитовое оборудование,
  поверочные установки) в справочник не идут: и список конкурентов, и перечень характеристик
  в ТЗ — целиком про электросчётчики.
- На странице категории одна карточка — это **базовая модель** (`<p><span>МИРТЕК-12-РУ-D17
  </span></p>`), под которой лежат ссылки-исполнения по заводам: «Таганрог», «Владивосток»,
  «Казахстан», «Беларусь». Каждое исполнение — отдельный URL со своим артикулом
  (`mirtek-12-ru-D17`, `mirtek-212-ru-d17`, `mirtek-12-kz-d1`, `mirtek-1-by-d1`).
- Российские исполнения — не косметический вариант одной и той же карточки: у «Таганрога»
  (`mirtek-12-ru-D17`) срок службы 48 лет, у «Владивостока» (`mirtek-212-ru-d17`) — 35 лет,
  и документы у них разные. Поэтому каждое сохраняется отдельной записью каталога.
- Казахстанские (`-kz-`) и белорусские (`-by-`) исполнения не собираются вовсе: для
  российских тендеров они нерелевантны.
- Пометка «Снят с производства» лежит в `<i>` рядом с названием модели — это прямой источник
  поля `Статус`.
- В карточке товара: блок `.productFeatures` («Ключевые особенности», неструктурированный
  текст), вкладка `.tabItem3` с парами ключ-значение («Характеристики»), вкладка `.tabItem1`
  с документами по группам («Разрешительные документы» / «Руководства» / «Прочая
  документация» / «Видеоинструкции»), вкладка `.tabItem4` («Условные обозначения»).
- Набор ключей во вкладке «Характеристики» у трёхфазных счётчиков **совпадает** с
  однофазными (13 одних и тех же полей, различаются только значения) — проверено на
  МИРТЕК-32-РУ-D37 и МИРТЕК-32-РУ-SP31. Парсер тем не менее универсальный: он забирает любые
  пары ключ-значение, а не заданный список, и неизвестные ключи не теряет (см.
  `app/services/catalog_site_sync.py`).
- Ссылки на документы ведут не на файл, а на `/download/<id>` с редиректом 302 — скачивание
  должно идти с `follow_redirects`.
- `robots.txt` сайта раздел `/produkciya/` не закрывает; закрыты `/search`, `/admin/`, `/en/`
  и служебные каталоги, которые адаптер и так не трогает.

Как и остальные адаптеры источников (раздел 5.1, 5.9 ТЗ): сбой на одной карточке или
категории не прерывает обход — ошибка складывается в `errors` и обход продолжается.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.base import PollError
from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify

BASE_URL = "https://mirtekgroup.com"

# Только электросчётчики (см. докстринг модуля). Ключ — значение поля `Тип прибора`
# в справочнике продукции, значение — URL категории на сайте.
CATEGORIES: dict[str, str] = {
    "Однофазный счётчик электроэнергии": f"{BASE_URL}/produkciya/odnofaznye-schyotchiki",
    "Трёхфазный счётчик электроэнергии": f"{BASE_URL}/produkciya/tryohfaznye-schyotchiki",
}

# Российские заводские исполнения — единственные, что попадают в справочник. Подпись ссылки
# на сайте («Таганрог»/«Владивосток») дублируется маркером страны в slug (`-ru-`), и
# проверяются оба: подпись — то, что видит человек, slug — то, что стабильно.
RUSSIAN_PLANT_LABELS = {"таганрог", "владивосток"}
_FOREIGN_SLUG_MARKERS = ("-kz-", "-by-")
_RUSSIAN_SLUG_MARKER = "-ru-"

DISCONTINUED_MARK = "снят с производства"

REQUEST_TIMEOUT_SECONDS = 30.0
# Вежливая пауза между запросами: обход собственного сайта компании, но нагружать его
# очередью из полусотни карточек подряд всё равно не нужно.
CRAWL_DELAY_SECONDS = 0.7

# Предел на объём текста «Ключевых особенностей», уходящего в модель. Блок на реальных
# карточках — 2-4 тыс. символов; предел защищает от карточки, где вёрстка «поехала» и в
# блок затянуло полстраницы.
MAX_FEATURES_CHARS = 20_000


@dataclass
class CatalogItem:
    """Одно заводское исполнение модели со страницы категории — то, что станет отдельной
    записью справочника (`Product`)."""

    model_name: str
    article: str
    # Подпись ссылки-исполнения на сайте («Таганрог» / «Владивосток»). `None` — подписи
    # не оказалось: гадать по slug нельзя, `mirtek-12` против `mirtek-212` — соглашение
    # сайта, а не правило.
    execution: str | None
    url: str
    category: str
    device_type: str
    discontinued: bool = False
    # Бейдж карточки на странице категории: «DIN-рейка» / «Сплит» / «Щиток». Это тип
    # монтажа — на карточке товара такого поля нет вовсе, а в справочнике оно есть.
    mounting_badge: str | None = None
    # Обозначение модели, вычлененное из наименования («i-PROM.1» из «Однофазный прибор
    # учета электроэнергии i-PROM.1»). В `model_name` при этом остаётся наименование как у
    # производителя — именно так прибор называют в закупочной документации, — а код нужен
    # для сопоставления с обозначением типа в Госреестре.
    model_code: str | None = None


@dataclass
class DocumentLink:
    group: str
    title: str
    url: str


@dataclass
class CatalogProductDetails:
    """Содержимое карточки товара."""

    url: str
    model_name: str | None = None
    description: str | None = None
    # Неструктурированный блок «Ключевые особенности» — уходит в AI-экстракцию целиком:
    # формулировки на сайте не унифицированы, регулярками их не разобрать.
    features_text: str | None = None
    # Пары ключ-значение вкладки «Характеристики» — как на сайте, без нормализации.
    # Маппинг на поля справочника делает сервис, а не адаптер.
    specifications: dict[str, str] = field(default_factory=dict)
    documents: list[DocumentLink] = field(default_factory=list)
    # Расшифровка кодировки артикула (вкладка «Условные обозначения»). В v1 сохраняется
    # текстом для справки — полный разбор структуры обозначения не требуется.
    symbol_legend: str | None = None


@dataclass
class CatalogOutcome:
    items: list[CatalogItem] = field(default_factory=list)
    errors: list[PollError] = field(default_factory=list)


def is_russian_execution(url: str, label: str | None) -> bool:
    """Российское ли это исполнение (Таганрог/Владивосток).

    Отдельная функция, потому что признак двойной и оба его источника по отдельности
    ненадёжны: подпись ссылки может отсутствовать (у «головной» ссылки карточки её нет),
    а slug у части старых позиций написан без маркера страны вовсе."""

    slug = urlparse(url).path.rsplit("/", 1)[-1].lower()
    if any(marker in slug for marker in _FOREIGN_SLUG_MARKERS):
        return False
    if _RUSSIAN_SLUG_MARKER in slug:
        return True
    return (label or "").strip().lower() in RUSSIAN_PLANT_LABELS


class MirtekCatalogAdapter:
    """Интерфейс — тот же, что у адаптеров источников (раздел 5.1 ТЗ): получить список
    объектов (`list_catalog`) → получить детали (`get_product_details`) → отдать ошибки
    вызывающему сервису, не прерывая обход."""

    source_key = "mirtek_site"

    def __init__(
        self,
        *,
        categories: dict[str, str] | None = None,
        crawl_delay: float = CRAWL_DELAY_SECONDS,
    ) -> None:
        self.categories = categories if categories is not None else dict(CATEGORIES)
        self.crawl_delay = crawl_delay
        self._session: httpx.Client | None = None

    # --- Уровни 1-2: категории и список карточек ---

    def list_catalog(self) -> CatalogOutcome:
        """Обходит обе категории электросчётчиков и возвращает все российские исполнения.

        Сбой одной категории не отменяет вторую (раздел 5.9 ТЗ): страницы независимы, и
        недоступность трёхфазного раздела не повод оставить справочник без однофазного."""

        outcome = CatalogOutcome()
        client = self._session_client()
        if client is not None:
            for device_type, category_url in self.categories.items():
                try:
                    html = self._get_html(client, category_url)
                except Exception as exc:  # noqa: BLE001 - изоляция сбоя одной категории
                    logger.warning(f"Каталог МИРТЕК: категория {category_url} не загружена: {exc}")
                    outcome.errors.append(PollError(category_url, f"Категория не загружена: {exc}"))
                    continue

                try:
                    items = parse_category(html, category_url, device_type=device_type)
                except Exception as exc:  # noqa: BLE001 - изменение вёрстки одной страницы
                    logger.warning(f"Каталог МИРТЕК: разметка категории {category_url} не разобрана: {exc}")
                    outcome.errors.append(PollError(category_url, f"Разметка категории не разобрана: {exc}"))
                    continue

                if not items:
                    # Пустая категория при живой странице — почти наверняка сменилась вёрстка,
                    # а не исчезла продукция. Молча вернуть ноль позиций нельзя: сервис
                    # синхронизации счёл бы, что все модели пропали с сайта.
                    outcome.errors.append(
                        PollError(
                            category_url,
                            "Страница категории загрузилась, но не содержит ни одной карточки — "
                            "вероятно, изменилась вёрстка сайта",
                        )
                    )
                outcome.items.extend(items)

        logger.info(
            f"Каталог МИРТЕК: собрано исполнений: {len(outcome.items)}, "
            f"ошибок обхода: {len(outcome.errors)}"
        )
        return outcome

    # --- Уровень 3: карточка товара ---

    def get_product_details(self, url: str) -> CatalogProductDetails:
        """Карточка одного исполнения. Исключение наружу не гасится: вызывающий сервис
        обрабатывает карточки в цикле и сам изолирует сбой конкретной позиции — так ошибка
        попадает в лог с привязкой к модели, а не безлико."""

        html = self._get_html(self._session_client(), url)
        return parse_product_card(html, url)

    def _session_client(self) -> httpx.Client:
        """Одно соединение на весь обход: TLS-хендшейк к сайту занимает секунды, а карточек
        в каталоге десятки — клиент на каждый запрос означал столько же хендшейков."""

        if self._session is None:
            self._session = self._client()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> "MirtekCatalogAdapter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=resolve_verify(BASE_URL),
        )

    def _get_html(self, client: httpx.Client, url: str) -> str:
        if self.crawl_delay:
            time.sleep(self.crawl_delay)
        response = fetch_with_retry(client, "GET", url)
        response.raise_for_status()
        return response.text


# --- Чистые функции разбора: без сети, тестируются на сохранённой разметке ---


def parse_category(html: str, category_url: str, *, device_type: str) -> list[CatalogItem]:
    """Карточки страницы категории → список российских исполнений.

    Разбирается структура `.listWrap`: в каждой ячейке `<p><span>МОДЕЛЬ</span>[<i>Снят с
    производства</i>]</p>`, затем соседние `<div>` со ссылками-исполнениями. Опора именно на
    `<p><span>`, а не на `aria-label` головной ссылки: `aria-label` есть и у ссылок на
    соцсети в подвале страницы."""

    soup = BeautifulSoup(html, "lxml")
    items: list[CatalogItem] = []
    seen_urls: set[str] = set()

    container = soup.select_one(".listWrap") or soup
    for name_block in container.find_all("p"):
        span = name_block.find("span")
        if span is None:
            continue
        model_name = _clean(span.get_text(" ", strip=True))
        if not model_name:
            continue

        discontinued = any(
            DISCONTINUED_MARK in _clean(tag.get_text(" ", strip=True)).lower()
            for tag in name_block.find_all("i")
        )

        card = name_block.parent
        if card is None:
            continue

        links = [a for a in card.find_all("a", href=True) if _is_product_link(a["href"])]
        if not links:
            continue

        badge_holder = card.parent if card.parent is not None else card
        badge_tag = badge_holder.select_one(".listBadge")
        badge = _clean(badge_tag.get_text(" ", strip=True)) if badge_tag else None

        for anchor in links:
            url = urljoin(category_url, anchor["href"])
            label = _clean(anchor.get_text(" ", strip=True))
            if not is_russian_execution(url, label):
                continue
            normalised = url.split("#", 1)[0].rstrip("/")
            if normalised in seen_urls:
                # «Головная» ссылка карточки и ссылка «Таганрог» ведут на один и тот же URL —
                # это одно исполнение, а не два.
                continue
            seen_urls.add(normalised)
            items.append(
                CatalogItem(
                    model_name=model_name,
                    article=normalised.rsplit("/", 1)[-1],
                    # Подпись ссылки («Таганрог»/«Владивосток») — единственный источник названия
                    # исполнения; гадать по slug нельзя (`mirtek-12` — Таганрог, `mirtek-212` —
                    # Владивосток, и это соглашение сайта, а не правило). Пусто — значит пусто.
                    execution=label or None,
                    url=normalised,
                    category=category_url,
                    device_type=device_type,
                    discontinued=discontinued,
                    mounting_badge=badge,
                )
            )

    return items


def parse_product_card(html: str, url: str) -> CatalogProductDetails:
    """Карточка товара → характеристики, документы, «Ключевые особенности», обозначения."""

    soup = BeautifulSoup(html, "lxml")
    details = CatalogProductDetails(url=url)

    heading = soup.find("h1")
    if heading is not None:
        details.model_name = _clean(heading.get_text(" ", strip=True))
        description_parts = []
        for sibling in heading.find_next_siblings("p"):
            text = _clean(sibling.get_text(" ", strip=True))
            if text:
                description_parts.append(text)
        if description_parts:
            details.description = "\n".join(description_parts)

    features = soup.select_one(".productFeatures")
    if features is not None:
        text = _block_text(features)
        details.features_text = text[:MAX_FEATURES_CHARS] or None

    # Универсальный разбор пар ключ-значение: берётся всё, что есть во вкладке, а не
    # заданный список полей (п.2.3 задания — набор характеристик у разных категорий может
    # оказаться шире, и терять лишние ключи нельзя).
    for pair in soup.select(".tabItem3 .productPropsWrap, .productPropsWrap"):
        cells = pair.find_all("div", recursive=False)
        if len(cells) < 2:
            continue
        key = _clean(cells[0].get_text(" ", strip=True))
        value = _clean(cells[1].get_text(" ", strip=True))
        if key and value and key not in details.specifications:
            details.specifications[key] = value

    details.documents = _parse_documents(soup, url)

    legend = soup.select_one(".tabItem4")
    if legend is not None:
        details.symbol_legend = _block_text(legend) or None

    return details


def _parse_documents(soup: BeautifulSoup, page_url: str) -> list[DocumentLink]:
    """Ссылки на документы с сохранением группы («Разрешительные документы», «Руководства»,
    «Прочая документация», «Видеоинструкции»).

    Группа — не украшение: по ней сервис отличает сертификат от руководства, не гадая по
    тексту ссылки. Заголовок группы — первый `<div>` блока, ссылки — во втором."""

    documents: list[DocumentLink] = []
    wrap = soup.select_one(".productDocsWrap")
    if wrap is None:
        return documents

    for block in wrap.find_all("div", recursive=False):
        children = block.find_all("div", recursive=False)
        if len(children) < 2:
            continue
        group = _clean(children[0].get_text(" ", strip=True))
        for anchor in children[1].find_all("a", href=True):
            title = _clean(anchor.get_text(" ", strip=True))
            # Размер файла идёт в тексте ссылки отдельным `<span>`: «… (pdf, 7.43 MB)».
            # В название документа он не нужен.
            title = re.sub(r"\s*\((?:pdf|doc|docx|zip|rar)[^)]*\)\s*$", "", title, flags=re.IGNORECASE)
            documents.append(
                DocumentLink(group=group, title=title, url=urljoin(page_url, anchor["href"]))
            )
    return documents


def _is_product_link(href: str) -> bool:
    path = urlparse(href).path
    # Ссылка на карточку товара — третий уровень: /produkciya/<категория>/<артикул>.
    # Ссылки на саму категорию и на английскую версию сюда не попадают.
    parts = [part for part in path.split("/") if part]
    return len(parts) == 3 and parts[0] == "produkciya"


def _block_text(node) -> str:
    """Текст блока с сохранением построчной структуры: списки и абзацы на сайте несут смысл
    («Основные интерфейсы связи:» и перечень под ним), и склейка их в одну строку заметно
    ухудшает качество последующей AI-экстракции."""

    lines = []
    for element in node.find_all(["p", "li", "div", "strong"]):
        if element.find(["p", "li"]) is not None:
            continue  # контейнер — его содержимое придёт отдельными строками
        text = _clean(element.get_text(" ", strip=True))
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return "\n".join(lines)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
