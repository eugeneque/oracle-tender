"""Полная карточка закупки в ЕИС: все вкладки извещения (раздел 5.1, 5.6 ТЗ).

Раньше система забирала из ЕИС только строку реестра — наименование, сумму, сроки — и
документы. Всё остальное, что видит человек, открывая закупку на `zakupki.gov.ru`, до
карточки тендера не доезжало: реквизиты и адрес заказчика, контактное лицо, порядок
предоставления документации, лоты, изменения и разъяснения, протоколы, договоры, журнал
событий. Из-за этого, в частности, у тендера не определялся регион — хотя на странице ЕИС
он есть и в адресе заказчика, и в его ИНН.

**Как устроена страница.** Извещение состоит из вкладок (`a.tabsNav__item`), их адреса
отличаются у 44-ФЗ и 223-ФЗ и содержат `noticeGuid`, которого нет в номере закупки, — поэтому
ссылки берутся из самой карточки, а не собираются по шаблону. Внутри вкладки данные лежат
секциями `section.common-text`: заголовок секции в `.common-text__caption`, пары
«поле-значение» — в `.common-text__title` и `.common-text__value`. Реквизиты (ИНН, КПП, ОГРН)
свёрстаны иначе: серая подпись и значение рядом, без отдельного заголовка.

Разбор намеренно неспецифичный: мы не перечисляем поля, которые хотим забрать, а забираем
все, что есть в секции. Перечень полей у ЕИС меняется между способами закупки и редакциями
формы, и жёсткий список молча терял бы половину карточки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from loguru import logger

from app.adapters.http_utils import fetch_with_retry

BASE_URL = "https://zakupki.gov.ru"

# Вкладки, которые забираем, и наши ключи для них. Совпадение по подстроке в подписи вкладки:
# у 44-ФЗ и 223-ФЗ они называются немного по-разному («Документы» / «Документы закупки»).
TAB_KEYS: list[tuple[str, str]] = [
    ("общая информация", "common"),
    ("список лотов", "lots"),
    ("документы", "documents"),
    ("изменения", "changes"),
    ("протокол", "protocols"),
    ("договор", "contracts"),
    ("журнал", "events"),
]

# Сколько строк таблицы забираем с одной вкладки: журнал событий у крупной закупки — сотни
# записей, а в карточке нужен обозримый список, а не полная выгрузка реестра.
MAX_TABLE_ROWS = 100


@dataclass
class CardSection:
    """Раздел карточки: заголовок и пары «поле — значение» в порядке страницы."""

    title: str
    fields: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class CardTable:
    """Табличная вкладка (лоты, протоколы, журнал событий): заголовки и строки как есть."""

    title: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class TenderCard:
    """Полная карточка закупки. `sections` — вкладка «Общая информация», `tables` — все
    остальные вкладки, `tab_urls` — адреса вкладок (интерфейс даёт по ним прямые ссылки)."""

    sections: list[CardSection] = field(default_factory=list)
    tables: dict[str, CardTable] = field(default_factory=dict)
    tab_urls: dict[str, str] = field(default_factory=dict)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_sections(html: str) -> list[CardSection]:
    """Разделы вкладки «Общая информация» со всеми парами «поле — значение».

    У 44-ФЗ и 223-ФЗ вёрстка разная — это два разных раздела сайта, а не разные шаблоны
    одного. У 223-ФЗ секция это `section.common-text` с парами `common-text__title/value`,
    у 44-ФЗ — `.blockInfo` с `.blockInfo__section` внутри и парами `section__title/info`.
    Разбираем обе: иначе половина закупок (а закупок 44-ФЗ в базе большинство) осталась бы
    без карточки, а значит и без региона.
    """

    soup = BeautifulSoup(html, "lxml")
    sections = _parse_common_text_sections(soup)
    sections.extend(_parse_block_info_sections(soup))
    return sections


def _parse_block_info_sections(soup: BeautifulSoup) -> list[CardSection]:
    """Разметка 44-ФЗ: `.blockInfo` → заголовок `.blockInfo__title`, поля `.blockInfo__section`."""

    sections: list[CardSection] = []

    for block in soup.select(".blockInfo"):
        caption = block.select_one(".blockInfo__title")
        if caption is None:
            continue

        section = CardSection(title=_clean(caption.get_text()))
        seen: set[tuple[str, str]] = set()

        for item in block.select(".blockInfo__section"):
            title_node = item.select_one(".section__title")
            value_node = item.select_one(".section__info")
            if title_node is None or value_node is None:
                continue
            name = _clean(title_node.get_text())
            value = _clean(value_node.get_text(" "))
            if name and value and (name, value) not in seen:
                seen.add((name, value))
                section.fields.append((name, value))

        if section.fields:
            sections.append(section)

    return sections


def _parse_common_text_sections(soup: BeautifulSoup) -> list[CardSection]:
    """Разметка 223-ФЗ: `section.common-text` с парами `common-text__title/value`."""

    sections: list[CardSection] = []

    for block in soup.select("section.common-text"):
        caption = block.select_one(".common-text__caption")
        if caption is None:
            continue

        section = CardSection(title=_clean(caption.get_text()))
        seen: set[tuple[str, str]] = set()

        for title_node in block.select(".common-text__title"):
            value_node = title_node.find_next_sibling(class_="common-text__value")
            if value_node is None:
                continue
            name = _clean(title_node.get_text())
            value = _clean(value_node.get_text(" "))
            if name and value and (name, value) not in seen:
                seen.add((name, value))
                section.fields.append((name, value))

        # Реквизиты (ИНН/КПП/ОГРН) свёрстаны парой «серая подпись + значение» без заголовка.
        for gray in block.select(".common-text__value--gray"):
            value_node = gray.find_next_sibling(class_="common-text__value")
            if value_node is None:
                continue
            name = _clean(gray.get_text())
            value = _clean(value_node.get_text(" "))
            if name and value and (name, value) not in seen:
                seen.add((name, value))
                section.fields.append((name, value))

        if section.fields:
            sections.append(section)

    return sections


def parse_table(html: str, title: str) -> CardTable:
    """Первая содержательная таблица вкладки.

    На вкладках ЕИС встречаются служебные таблицы вёрстки, поэтому берётся та, у которой есть
    заголовки и хотя бы одна строка данных.
    """

    soup = BeautifulSoup(html, "lxml")
    table = CardTable(title=title)

    for candidate in soup.select("table"):
        headers = [_clean(cell.get_text()) for cell in candidate.select("thead th, thead td")]
        body_rows = candidate.select("tbody tr") or candidate.select("tr")[1:]
        rows: list[list[str]] = []

        for row in body_rows[:MAX_TABLE_ROWS]:
            cells = [_clean(cell.get_text(" ")) for cell in row.select("td, th")]
            if any(cells):
                rows.append(cells)

        if headers and rows:
            table.headers = headers
            table.rows = rows
            return table

    # Таблицы нет — часть вкладок при пустом разделе показывает только текст-заглушку
    # («Информация отсутствует»). Возвращаем её как одну строку: пустая вкладка и вкладка,
    # которую не удалось разобрать, — разные вещи, и пользователь должен их различать.
    notice = soup.select_one(".no-data, .search-empty, .notFoundMessage")
    if notice is not None:
        table.rows = [[_clean(notice.get_text())]]
    return table


def _card_items(row: Tag) -> list[str]:
    """Значения из «карточной» вёрстки вкладки (ЕИС верстает часть списков не таблицей)."""

    return [
        _clean(node.get_text(" "))
        for node in row.select(".registry-entry__body-value, .common-text__value")
        if _clean(node.get_text(" "))
    ]


def fetch_card(client, common_info_url: str) -> TenderCard:
    """Собирает карточку целиком: общая информация плюс все прочие вкладки.

    Ошибка одной вкладки не отменяет остальные (раздел 5.9 ТЗ): у части закупок протоколов
    или договоров нет вовсе, и вкладка может отвечать ошибкой — карточка от этого не должна
    остаться пустой.
    """

    card = TenderCard()

    response = fetch_with_retry(client, "GET", common_info_url)
    response.raise_for_status()
    common_html = response.text

    card.sections = parse_sections(common_html)
    card.tab_urls["common"] = common_info_url

    soup = BeautifulSoup(common_html, "lxml")
    tabs: dict[str, tuple[str, str]] = {}
    for tab in soup.select("a.tabsNav__item"):
        label = _clean(tab.get_text()).lower()
        href = tab.get("href") or ""
        if not href:
            continue
        for marker, key in TAB_KEYS:
            if marker in label and key not in tabs:
                tabs[key] = (_clean(tab.get_text()), urljoin(BASE_URL, href))
                break

    for key, (label, url) in tabs.items():
        card.tab_urls[key] = url
        if key in ("common", "documents"):
            # Документы забираются отдельным путём (`EisAdapter.download_documents`) — там же
            # они и скачиваются; дублировать их таблицей незачем.
            continue
        try:
            tab_response = fetch_with_retry(client, "GET", url)
            tab_response.raise_for_status()
            card.tables[key] = parse_table(tab_response.text, label)
        except Exception as exc:  # noqa: BLE001 - одна вкладка не должна ронять всю карточку
            logger.warning(f"Вкладка «{label}» карточки ЕИС не получена: {exc}")

    return card
