"""Адаптер ЕЭТП / zakazrf (etp.zakazrf.ru) — раздел 4.1 (источник №12), 5.1 ТЗ.

Сводный реестр извещений (`/NotificationEx`) сам по себе — обычный серверный HTML (ASP.NET,
без клиентского JS-фреймворка вроде Vue/Next.js). Но быстрый поиск ("Filter.FastFilter")
реализован через AJAX-постбэк собственного грид-фреймворка площадки ("ORM"): поля формы
получают динамический суффикс ID на каждой загрузке страницы, а сам контракт запроса
(обязательные скрытые поля, порядок их отправки) нигде не задокументирован — воспроизвести
его напрямую через httpx можно, но ненадёжно (первая проверка через простой GET-параметр
`?Filter.FastFilter=...` результата не дала — `TotalRows` в ответе не менялся). Без фильтра
реестр отдаёт ~1.5 млн записей за всю историю площадки, импортировать это целиком нельзя.

Поэтому адаптер использует Playwright: реально открывает страницу, вводит ключевое слово в
поле быстрого поиска и нажимает «Найти», как это сделал бы пользователь, затем разбирает
получившуюся HTML-таблицу тем же способом (BeautifulSoup), что и остальные адаптеры. Это
устойчивее к смене конкретного контракта AJAX-постбэка площадки, так как использует только
стабильный публичный UI (`input[name='Filter.FastFilter']`, кнопка «Найти»).

Выдача обходится постранично (`_search_pages`) — грид показывает по 20 строк, и разбор одной
только первой страницы терял остальные результаты запроса. Отсечения по дате, как в адаптере
ЕИС, здесь нет: грид не даёт устойчивой сортировки по дате обновления.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

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

BASE_URL = "https://etp.zakazrf.ru"

# Расширения, которые считаем документами закупки. Список закрытый, а не «всё, что не
# страница»: на карточке есть и ссылки на подписи (`/File/Signature/...`), и навигация, и
# они не должны попадать в документы тендера.
_DOCUMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".zip", ".rar", ".rtf", ".txt", ".7z",
)
NOTIFICATIONS_PATH = "/NotificationEx"

# Кнопка «Вперед» серверной пагинации грида; на последней странице грид перестаёт
# обновляться (класс `disabled` площадка проставляет не всегда — см. `_search_pages`).
NEXT_PAGE_SELECTOR = "a.pager-button-next"
# Защитный предел обхода — тот же принцип, что и в остальных адаптерах (см. app/adapters/eis.py).
MAX_SEARCH_PAGES = 20


def _grid_signature(html: str) -> str:
    """Отпечаток содержимого грида — по нему видно, сменилась ли страница после клика
    «Вперед». Сравнивать целиком `page.content()` нельзя: в разметке есть меняющиеся от
    запроса к запросу служебные токены постбэка, из-за которых страница всегда выглядела бы
    новой."""

    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.reporttable")
    if table is None:
        return ""
    return "|".join(element_text(cell) or "" for cell in table.select("tr td:nth-of-type(3)"))


DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# Состояние закупки (2-я колонка таблицы) → внутренний статус (раздел 7 ТЗ, `Tender.status`).
# Список неполный: площадка использует больше формулировок, чем удалось увидеть на выборке при
# разработке — нераспознанная формулировка просто оставляет статус пустым, а не угадывается.
_STATE_TO_STATUS = {
    "идет подача заявок на участие": "collecting_bids",
    "рассмотрение заявок": "evaluation",
    "подведение итогов": "evaluation",
    "закупка завершена": "completed",
    "закупка отменена": "cancelled",
}


def _parse_status(state_text: str | None) -> str | None:
    if not state_text:
        return None
    key = state_text.strip().lower().rstrip(".")
    return _STATE_TO_STATUS.get(key)


def _document_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=40.0,
        verify=resolve_verify(BASE_URL),
        follow_redirects=True,
    )


def _parse_document_links(html: str) -> list[DocumentRef]:
    """Ссылки на документы с карточки извещения.

    Имя файла берётся из текста ссылки: в самом URL у ЕИС только идентификатор (`?uid=...`),
    по которому ни человек не поймёт, что за файл, ни код не определит формат для разбора.
    """

    soup = BeautifulSoup(html, "lxml")
    documents: list[DocumentRef] = []
    seen: set[str] = set()

    for link in soup.select("a[href]"):
        href = (link.get("href") or "").strip()
        name = link.get_text(strip=True)
        if not href or not name:
            continue
        if not name.lower().endswith(_DOCUMENT_EXTENSIONS):
            continue

        url = urljoin(BASE_URL, href)
        if url in seen:
            continue
        seen.add(url)
        documents.append(DocumentRef(file_name=name, url=url))

    return documents


class ZakazrfAdapter(SourceAdapter):
    source_key = "zakazrf"

    def __init__(self, *, search_keywords: list[str] | None = None) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS

    def _search_html(self, keyword: str) -> str:
        """Первая страница выдачи — отдельным методом ради простых проверок разбора; полный
        обход страниц делает `_search_pages`."""

        for html in self._search_pages(keyword, max_pages=1):
            return html
        return ""

    def _search_pages(self, keyword: str, max_pages: int = MAX_SEARCH_PAGES):
        """Все страницы выдачи по ключевому слову.

        Грид отдаёт по 20 строк, а адаптер раньше забирал только первую страницу — всё
        остальное по запросу терялось. Пагинация серверная, через AJAX-постбэк по кнопке
        «Вперед» (`a.pager-button-next`); прямого URL со страницей у грида нет, поэтому
        листаем кликом в той же сессии. Конец выдачи определяем по неизменившемуся
        содержимому: на последней странице кнопка остаётся кликабельной, но грид уже не
        обновляется."""

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
                page.goto(
                    f"{BASE_URL}{NOTIFICATIONS_PATH}", wait_until="networkidle", timeout=30000
                )
                page.fill("input[name='Filter.FastFilter']", keyword)
                page.click("button:has-text('Найти')")
                page.wait_for_load_state("load", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(1000)  # грид обновляется после AJAX-постбэка

                previous_html = None
                for _ in range(max_pages):
                    # `page.content()` изредка попадает в момент, когда страница ещё
                    # донавигирует после постбэка (Playwright тогда кидает ошибку) — одна
                    # короткая повторная попытка вместо усложнения условий ожидания выше.
                    try:
                        html = page.content()
                    except Exception:
                        page.wait_for_timeout(1500)
                        html = page.content()

                    grid = _grid_signature(html)
                    if previous_html is not None and grid == previous_html:
                        break  # грид не сменился — выдача исчерпана
                    previous_html = grid
                    yield html

                    next_button = page.query_selector(NEXT_PAGE_SELECTOR)
                    if next_button is None:
                        break
                    classes = next_button.get_attribute("class") or ""
                    if "disabled" in classes:
                        break
                    next_button.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    page.wait_for_timeout(800)
            finally:
                browser.close()

    def _parse_row(self, row, errors: list[PollError]) -> TenderSummary | None:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 12:
            return None  # не строка данных (например, заголовок или служебная строка)

        link = cells[1].find("a")
        if link is None:
            return None
        external_id = element_text(link)
        if not external_id:
            return None

        try:
            law = element_text(cells[0])
            method = element_text(cells[3])
            procurement_method = " ".join(part for part in (law, method) if part) or None

            detail_href = link.get("href", "")
            source_url = (
                urljoin(BASE_URL, detail_href) if detail_href else f"{BASE_URL}{NOTIFICATIONS_PATH}"
            )

            organizer_name = element_text(cells[6])
            # У части извещений по 223-ФЗ колонка «Заказчик» пустая, заполнен только
            # «Организатор» — тот же случай, что и с 44-ФЗ-карточками ЕИС без заказчика
            # (см. app/adapters/eis.py): берём организатора как ближайший доступный аналог.
            customer_name = element_text(cells[7]) or organizer_name

            return TenderSummary(
                external_id=external_id,
                title=element_text(cells[4]) or "(без наименования)",
                source_url=source_url,
                customer_name=customer_name,
                organizer_name=organizer_name,
                procurement_method=procurement_method,
                status=_parse_status(element_text(cells[2])),
                price=parse_price(element_text(cells[5])),
                currency="RUB",
                publish_date=parse_ru_date(element_text(cells[9])),
                application_end=self._parse_deadline(element_text(cells[11])),
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одной строки не должна прервать разбор
            errors.append(PollError(external_id, f"Не удалось разобрать строку: {exc}"))
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
            found_table = False
            try:
                for html in self._search_pages(keyword):
                    table = BeautifulSoup(html, "lxml").select_one("table.reporttable")
                    if table is None:
                        break
                    found_table = True

                    # Без `recursive=False`: после AJAX-обновления `page.content()` сериализует
                    # DOM уже с неявным `<tbody>`, которого не было в исходном серверном HTML —
                    # со строгим `recursive=False` это ломало поиск строк (находило 0 вместо
                    # реальных данных).
                    for row in table.find_all("tr"):
                        if "orm-grid-table-header" in (row.get("class") or []):
                            continue
                        summary = self._parse_row(row, outcome.errors)
                        if summary is not None:
                            self._collect(seen, summary)
            except Exception as exc:  # noqa: BLE001 - ошибка одного ключевого слова не должна прервать остальные
                outcome.errors.append(
                    PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                )
                continue

            if not found_table:
                outcome.errors.append(
                    PollError(None, f"Таблица результатов не найдена в выдаче по '{keyword}'")
                )

        outcome.tenders = list(seen.values())
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        outcome = self.list_new_tenders(since=None)
        for summary in outcome.tenders:
            if summary.external_id == external_id:
                return TenderDetails(**summary.__dict__)
        raise ValueError(f"Тендер {external_id} не найден в выдаче ЕЭТП")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Реальные файлы документации с карточки извещения.

        Карточка `/NotificationEx/id/<N>` — обычный серверный HTML (тот же ASP.NET, что и
        реестр), документы в нём лежат прямыми ссылками с читаемыми именами. По 44-ФЗ файлы
        физически хранятся в файловом хранилище ЕИС (`zakupki.gov.ru/44fz/filestore/...`), по
        223-ФЗ — на самой площадке; для нас разницы нет, скачивание одинаковое, а TLS для
        домена ЕИС уже настроен (`app/adapters/http_utils.resolve_verify`).

        Playwright здесь не нужен, в отличие от реестра: карточка отдаётся сервером целиком.
        """

        # Адрес карточки берём из базы, если он есть: поиск по реестру здесь означал бы
        # прогон Playwright по всем страницам выдачи ради одной ссылки.
        card_url = source_url or self.get_tender_details(external_id).source_url
        response = fetch_with_retry(
            _document_client(), "GET", card_url, headers={"Accept": "text/html"}
        )
        response.raise_for_status()
        documents = _parse_document_links(response.text)
        return documents or fetch_eis_documents(external_id)
