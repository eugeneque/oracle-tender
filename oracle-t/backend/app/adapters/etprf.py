"""Адаптер ЭТП РФ (etprf.ru) — раздел 4.1 (источник №9), 5.1 ТЗ.

Реестр извещений (`web.etprf.ru/NotificationCR`) построен на том же движке таблиц («ORM»,
класс `reporttable`, поля формы `Filter.FastFilter` с динамическим ID), что и ЕЭТП/zakazrf
(`app/adapters/zakazrf.py`) — похоже, оба используют общий шаблон площадки. По тем же причинам
(недокументированный AJAX-контракт быстрого поиска) адаптер использует Playwright — вводит
ключевое слово и жмёт «Найти», а не воспроизводит запрос напрямую.

TLS-особенность (см. также `app/adapters/http_utils.resolve_verify`): сервер `etprf.ru` не
досылает промежуточный сертификат `GlobalSign GCC R6 AlphaSSL CA 2025`, из-за чего обычная
проверка TLS-цепочки падает. Решение уже встроено в `resolve_verify` — playwright запускает
свой собственный Chromium (у него отдельный, встроенный список доверенных CA, эта проблема
конкретно для Playwright не актуальна, но тот же `resolve_verify` используется пингом
доступности источника через httpx).

Статус извещения («Статус извещения» в таблице) — только "Опубликован"/"Отменен" на выборке
при разработке, и "Опубликован" НЕ означает "сейчас идёт приём заявок": среди строк со статусом
"Опубликован" встречаются извещения с истёкшим на годы сроком подачи заявок — статус, похоже,
просто фиксирует факт публикации и не меняется после закрытия приёма заявок. Поэтому в
`Tender.status` размечается только "Отменен" → `cancelled`, остальное остаётся пустым, а не
угадывается.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

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
from app.adapters.http_utils import DEFAULT_USER_AGENT
from app.adapters.parsing_utils import element_text, parse_price, parse_ru_date

BASE_URL = "https://web.etprf.ru"
NOTIFICATIONS_PATH = "/NotificationCR?Sources=1"
# Тот же грид-движок, что и у ЕЭТП (см. app/adapters/zakazrf.py): серверная пагинация по
# кнопке «Вперед», прямого URL со страницей нет.
NEXT_PAGE_SELECTOR = "a.pager-button-next"
MAX_SEARCH_PAGES = 20

# Быстрый фильтр площадки соединяет слова запроса через И (с учётом словоформ: «счетчики»
# находит «счетчика частиц»). Общий для остальных адаптеров набор из длинных фраз здесь
# поэтому давал стабильный ноль — «счетчик электрической энергии» требует все три слова
# сразу в одном наименовании, а таких извещений на площадке просто нет. Причём выдача при
# этом выглядела как штатная пустая: ноль тендеров, ноль ошибок, — то есть источник молча
# не работал. Набор ниже — короткие запросы, подобранные по живой выдаче: вместе они дают
# те извещения, которые здесь действительно публикуются («замена узлов учета
# электроэнергии», «монтаж приборов учета энергоресурсов»).
DEFAULT_SEARCH_KEYWORDS = [
    "счетчик",
    "прибор учета",
    "узел учета",
    "учет электроэнергии",
]

_STATUS_TO_STATUS = {
    "отменен": "cancelled",
}


def _grid_signature(html: str) -> str:
    """Отпечаток содержимого грида — по нему видно, сменилась ли страница после клика
    «Вперед» (см. одноимённую функцию в app/adapters/zakazrf.py)."""

    table = BeautifulSoup(html, "lxml").select_one("table.reporttable")
    if table is None:
        return ""
    return "|".join(element_text(cell) or "" for cell in table.select("tr td:nth-of-type(3)"))


def _parse_status(state_text: str | None) -> str | None:
    if not state_text:
        return None
    return _STATUS_TO_STATUS.get(state_text.strip().lower())


class EtprfAdapter(SourceAdapter):
    source_key = "etprf"

    def __init__(self, *, search_keywords: list[str] | None = None) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS

    def _search_html(self, keyword: str) -> str:
        """Первая страница выдачи — отдельным методом ради простых проверок разбора; полный
        обход страниц делает `_search_pages`."""

        for html in self._search_pages(keyword, max_pages=1):
            return html
        return ""

    def _search_pages(self, keyword: str, max_pages: int = MAX_SEARCH_PAGES):
        """Все страницы выдачи по ключевому слову — грид показывает по 20 строк, разбор одной
        первой страницы терял остальные (та же правка, что и в app/adapters/zakazrf.py)."""

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
                page.wait_for_timeout(1000)

                previous_signature = None
                for _ in range(max_pages):
                    try:
                        html = page.content()
                    except Exception:
                        page.wait_for_timeout(1500)
                        html = page.content()

                    signature = _grid_signature(html)
                    if previous_signature is not None and signature == previous_signature:
                        break  # грид не сменился — выдача исчерпана
                    previous_signature = signature
                    yield html

                    next_button = page.query_selector(NEXT_PAGE_SELECTOR)
                    if next_button is None:
                        break
                    if "disabled" in (next_button.get_attribute("class") or ""):
                        break
                    next_button.click()
                    page.wait_for_load_state("networkidle", timeout=30000)
                    page.wait_for_timeout(800)
            finally:
                browser.close()

    def _parse_row(self, row, errors: list[PollError]) -> TenderSummary | None:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 11:
            return None  # не строка данных

        external_id = element_text(cells[2])
        if not external_id:
            return None

        try:
            link = cells[-1].find("a")
            detail_href = link.get("href", "") if link else ""
            source_url = (
                urljoin(BASE_URL, detail_href) if detail_href else f"{BASE_URL}{NOTIFICATIONS_PATH}"
            )

            return TenderSummary(
                external_id=external_id,
                title=element_text(cells[3]) or "(без наименования)",
                source_url=source_url,
                customer_name=element_text(cells[7]),
                organizer_name=element_text(cells[7]),
                procurement_method=element_text(cells[0]),
                status=_parse_status(element_text(cells[10])),
                price=parse_price(element_text(cells[4])),
                currency="RUB",
                publish_date=parse_ru_date(element_text(cells[8])),
                application_end=self._parse_deadline(element_text(cells[9])),
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
                    # DOM уже с неявным `<tbody>` (см. замечание в app/adapters/zakazrf.py).
                    for row in table.find_all("tr"):
                        if "orm-grid-table-header" in (row.get("class") or []):
                            continue
                        summary = self._parse_row(row, outcome.errors)
                        if summary is not None:
                            seen[summary.external_id] = summary
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
        raise ValueError(f"Тендер {external_id} не найден в выдаче ЭТП РФ")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Документация закупки из ЕИС по её реестровому номеру.

        Своего перечня файлов у площадки взять неоткуда: перечень файлов на карточке отдаётся только после входа.
        Но закупки 44-ФЗ и 223-ФЗ публикуются в ЕИС по закону, и там документация открыта —
        см. `app/adapters/eis_documents.py`. Для коммерческих закупок площадки (без номера
        ЕИС) документов не будет: их публикуют только в личном кабинете.
        """

        return fetch_eis_documents(external_id)
