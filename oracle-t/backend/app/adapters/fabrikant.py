"""Адаптер Фабрикант (fabrikant.ru) — раздел 4.1 (источник №3), 5.1 ТЗ.

Поиск процедур (`/procedure/search`) — Next.js-приложение (App Router): карточки результатов
не присутствуют в исходном серверном HTML, а подгружаются клиентским кодом после гидратации
(в сетевых запросах страницы нет отдельного стабильного JSON-эндпоинта — только внутренний
RSC-формат Next.js, завязанный на билд). Поэтому, как и в `app/adapters/zakazrf.py` и
`app/adapters/sberbank_ast.py`, адаптер использует Playwright: открывает страницу поиска с
ключевым словом в query-параметре (тот же URL, что формирует строка поиска на сайте), ждёт
отрисовки карточек и разбирает получившуюся HTML-разметку (`div[data-slot="card"]`).

Один и тот же URL (`/procedure/search`, вкладка «Все») отдаёт вперемешку и закупки, и продажи
имущества — не фильтруем по вкладке, чтобы не терять закупки, у которых тип процедуры не
отражён явно во вкладке (наблюдалось на выборке при разработке).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.adapters.eis_documents import fetch_eis_documents
from app.adapters.base import (
    DocumentRef,
    PollError,
    PollOutcome,
    SourceAdapter,
    TenderDetails,
    TenderSummary,
)
from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify
from app.adapters.parsing_utils import element_text, parse_price, parse_ru_date

BASE_URL = "https://www.fabrikant.ru"
SEARCH_PATH = "/procedure/search"
CARD_SELECTOR = 'div[data-slot="card"][data-id]'
# Кнопка «следующая страница» пагинации (`rc-pagination`); на последней странице получает
# класс `rc-pagination-disabled`.
NEXT_PAGE_SELECTOR = "li.rc-pagination-next"
# Защитный предел обхода — тот же принцип, что и в остальных адаптерах (см. app/adapters/eis.py).
MAX_SEARCH_PAGES = 20

DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# Статус процедуры на площадке передаётся одним из бейджей карточки вперемешку с прочими
# (категория закупки, «МСП» и т.п.) — бейджи ищем по тексту, а не по индексу (см. `_parse_status`).
# Формулировки собраны по выборке при разработке (фильтр «Статус процедуры» на самом сайте);
# нераспознанная формулировка статус не угадывает, а оставляет пустым — тот же принцип, что и
# в остальных адаптерах.
_STATUS_TO_STATUS = {
    "идёт приём заявок": "collecting_bids",
    "прием заявок": "collecting_bids",
    "приём заявок": "collecting_bids",
    "работа комиссии": "evaluation",
    "ожидает решения организатора": "evaluation",
    "ожидание решения организатора (подведение итогов)": "evaluation",
    "закончено": "completed",
    "завершена": "completed",
    "завершено": "completed",
    "отказ организатора": "cancelled",
    "организатор отказался": "cancelled",
    "торги не состоялись": "cancelled",
    "закончено (несостоявшаяся процедура)": "cancelled",
}


def _parse_status(card) -> str | None:
    for badge in card.select('span[data-slot="badge"]'):
        text = element_text(badge)
        if not text:
            continue
        mapped = _STATUS_TO_STATUS.get(text.strip().lower())
        if mapped:
            return mapped
    return None


def _field_value(card, label: str) -> str | None:
    """Значение пары «подпись – значение» в теле карточки (например, «Организатор» или
    «Дата окончания приёма заявок») — подписи идут не в фиксированном наборе (у части карточек
    нет заказчика или срока подачи), поэтому ищем по тексту подписи, а не по индексу блока."""

    for label_el in card.select('div[data-slot="text"].font-semibold'):
        if element_text(label_el) == label:
            return element_text(label_el.find_next_sibling())
    return None


def _price(card) -> str | None:
    # Блок с ценой — единственный `div.mb-6` внутри правой колонки карточки (`.mb-6` сам по
    # себе неоднозначен: тем же классом помечен и верхний колонтитул карточки), поэтому сначала
    # сужаем поиск до правой колонки по её собственному набору классов.
    aside = card.select_one("div.w-full.shrink-0.flex-col")
    if aside is None:
        return None
    return element_text(aside.select_one("div.mb-6"))


def _document_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=40.0,
        verify=resolve_verify(BASE_URL),
        follow_redirects=True,
    )


def _find_documentation_url(card_html: str) -> str | None:
    """Адрес страницы с перечнем документов, взятый с карточки процедуры."""

    soup = BeautifulSoup(card_html, "lxml")
    for link in soup.select("a[href]"):
        href = link.get("href") or ""
        if "file_documentations_view" in href:
            return urljoin(BASE_URL, href)
    return None


def _parse_documentation_page(html: str) -> list[DocumentRef]:
    """Файлы со страницы документации.

    Имя файла приклеено к тексту кнопки («Скачать файлзапрос ЭТКП.docx»), поэтому префикс
    отрезается: без этого имя документа в карточке начиналось бы со слова «Скачать файл», а
    расширение всё равно определялось бы по хвосту.
    """

    soup = BeautifulSoup(html, "lxml")
    documents: list[DocumentRef] = []
    seen: set[str] = set()

    for link in soup.select("a[href]"):
        href = link.get("href") or ""
        if "file_documentations_get_file" not in href:
            continue

        url = urljoin(BASE_URL, href)
        if url in seen:
            continue
        seen.add(url)

        name = link.get_text(strip=True)
        for prefix in ("Скачать файл", "Скачать"):
            if name.startswith(prefix):
                name = name[len(prefix) :].strip()
                break
        documents.append(DocumentRef(file_name=name or "Документ (Фабрикант)", url=url))

    return documents


class FabrikantAdapter(SourceAdapter):
    source_key = "fabrikant"

    def __init__(self, *, search_keywords: list[str] | None = None) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS

    def _search_html(self, keyword: str) -> str:
        """Первая страница выдачи. Оставлена отдельным методом ради простых проверок разбора —
        полный обход страниц делает `_search_pages`."""

        for html in self._search_pages(keyword, max_pages=1):
            return html
        return ""

    def _search_pages(self, keyword: str, max_pages: int = MAX_SEARCH_PAGES):
        """Все страницы выдачи по ключевому слову.

        Раньше адаптер читал только первую страницу — десять карточек, тогда как площадка по
        тому же запросу показывает «Всего: 707». Пагинация здесь чисто клиентская
        (`rc-pagination`): ни `page`, ни `offset` в URL не влияют на выдачу — проверено, все
        варианты возвращают первую страницу, — поэтому листать приходится кликом по кнопке
        «вперёд» в той же сессии браузера. Признак последней страницы — класс `disabled`
        на этой кнопке.
        """

        url = f"{BASE_URL}{SEARCH_PATH}?query={quote(keyword)}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=30000)
                try:
                    page.wait_for_selector(CARD_SELECTOR, timeout=10000)
                except Exception:
                    return  # по этому ключевому слову результатов нет — не фатально

                for _ in range(max_pages):
                    yield page.content()

                    next_button = page.query_selector(NEXT_PAGE_SELECTOR)
                    if next_button is None:
                        break
                    classes = next_button.get_attribute("class") or ""
                    if "disabled" in classes:
                        break

                    first_id = page.get_attribute(CARD_SELECTOR, "data-id")
                    next_button.click()
                    try:
                        # Ждём именно смены содержимого: карточки перерисовываются на месте,
                        # и `networkidle` сам по себе не гарантирует, что данные уже новые.
                        page.wait_for_function(
                            "id => document.querySelector('div[data-slot=\"card\"][data-id]')"
                            "?.getAttribute('data-id') !== id",
                            arg=first_id,
                            timeout=15000,
                        )
                    except Exception:
                        break  # страница не сменилась — считаем выдачу исчерпанной
            finally:
                browser.close()

    def _parse_card(self, card, errors: list[PollError]) -> TenderSummary | None:
        link = card.select_one('a[data-slot="anchor"]')
        number_span = card.select_one("span.flex.min-h-6")
        if link is None or number_span is None:
            return None

        spans = number_span.find_all("span", recursive=False)
        if len(spans) < 2:
            return None
        procurement_method = element_text(spans[0])
        external_id = element_text(spans[1])
        if not external_id:
            return None
        external_id = external_id.lstrip("№").strip()

        try:
            organizer_name = _field_value(card, "Организатор")
            customer_name = _field_value(card, "Заказчик") or organizer_name

            return TenderSummary(
                external_id=external_id,
                title=element_text(link) or "(без наименования)",
                source_url=link.get("href") or f"{BASE_URL}{SEARCH_PATH}",
                customer_name=customer_name,
                organizer_name=organizer_name,
                procurement_method=procurement_method,
                status=_parse_status(card),
                price=parse_price(_price(card)),
                currency="RUB",
                publish_date=parse_ru_date(_field_value(card, "Дата публикации")),
                application_end=self._parse_deadline(
                    _field_value(card, "Дата окончания приёма заявок")
                ),
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одной карточки не должна прервать разбор
            errors.append(PollError(external_id, f"Не удалось разобрать карточку: {exc}"))
            return None

    @staticmethod
    def _parse_deadline(text: str | None) -> datetime | None:
        deadline_date = parse_ru_date(text)
        if deadline_date is None:
            return None
        return datetime(deadline_date.year, deadline_date.month, deadline_date.day)

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}

        for keyword in self.search_keywords:
            found_any = False
            try:
                for html in self._search_pages(keyword):
                    cards = BeautifulSoup(html, "lxml").select(CARD_SELECTOR)
                    if not cards:
                        break
                    found_any = True
                    for card in cards:
                        summary = self._parse_card(card, outcome.errors)
                        if summary is not None:
                            self._collect(seen, summary)
            except Exception as exc:  # noqa: BLE001 - ошибка одного ключевого слова не должна прервать остальные
                outcome.errors.append(
                    PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                )
                continue

            if not found_any:
                outcome.errors.append(
                    PollError(None, f"Результаты поиска не найдены по '{keyword}'")
                )

        outcome.tenders = list(seen.values())
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        outcome = self.list_new_tenders(since=None)
        for summary in outcome.tenders:
            if summary.external_id == external_id:
                return TenderDetails(**summary.__dict__)
        raise ValueError(f"Тендер {external_id} не найден в выдаче Фабрикант")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Реальные файлы документации закупки.

        На карточке процедуры файлов нет — там только ссылка «Документация по мониторингу»
        на отдельную страницу (`action=file_documentations_view`), и уже она отдаёт перечень
        со ссылками вида `action=file_documentations_get_file&document_id=<N>`. Отсюда два
        запроса вместо одного: сначала карточка, чтобы узнать адрес страницы документации
        (её параметр `procedure_id` — внутренний идентификатор площадки, не совпадающий с
        номером процедуры), потом сама страница.

        Обе страницы отдаются сервером как обычный HTML, Playwright нужен только реестру.
        """

        card_url = source_url or self.get_tender_details(external_id).source_url

        card = fetch_with_retry(
            _document_client(), "GET", card_url, headers={"Accept": "text/html"}
        )
        card.raise_for_status()

        documentation_url = _find_documentation_url(card.text)
        if documentation_url is None:
            # Раздела документации на карточке нет — пробуем ЕИС по реестровому номеру.
            return fetch_eis_documents(external_id)

        listing = fetch_with_retry(
            _document_client(), "GET", documentation_url, headers={"Accept": "text/html"}
        )
        listing.raise_for_status()
        documents = _parse_documentation_page(listing.text)
        return documents or fetch_eis_documents(external_id)
