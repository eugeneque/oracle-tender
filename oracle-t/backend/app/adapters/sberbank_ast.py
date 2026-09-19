"""Адаптер Сбербанк-АСТ (sberbank-ast.ru) — раздел 4.1 (источник №10), 5.1 ТЗ.

Сводный реестр закупок (`/UnitedPurchaseList.html`) — Vue-приложение (Element Plus), список
процедур не рендерится на сервере. При наблюдении за сетевыми запросами страницы нашёлся общий
эндпоинт `POST /api/Processing/main`, но это не публичный REST API вроде найденного у ЭТП ГПБ
(`app/adapters/etpgpb.py`), а внутренний RPC-шлюз с собственным протоколом (`windowCode`,
`actionType` MONITOR/TEMPLATE, XML внутри `filterBody`, зависящий от состояния сессии) —
воспроизводить его напрямую слишком ненадёжно. Поэтому адаптер использует Playwright: реально
вводит ключевое слово в поле поиска и нажимает Enter, как пользователь, затем разбирает
получившуюся HTML-разметку карточек (`div.purchase-card`).
"""

from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from loguru import logger
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

BASE_URL = "https://www.sberbank-ast.ru"
LIST_PATH = "/UnitedPurchaseList.html"
SEARCH_INPUT_SELECTOR = "input[placeholder='Введите ключевые слова или номер процедуры']"
# Кнопка «следующая страница» пагинации Element UI; на последней странице получает
# атрибут `disabled`.
NEXT_PAGE_SELECTOR = "button.btn-next"
CARD_CODE_SELECTOR = "a.purchase-card__purchcode"
# Защитный предел обхода — тот же принцип, что и в остальных адаптерах (см. app/adapters/eis.py).
MAX_SEARCH_PAGES = 20

# Таблица файлов документации закупки на карточке. Идентификатор у неё стабильный
# (`PurchaseDocumentationInfo_DocFilesRO`), а вот классы — общие для всех таблиц страницы,
# поэтому цепляемся именно за него.
DOC_TABLE_SELECTOR = "table[id^='PurchaseDocumentationInfo']"
# Имя файла в ячейке таблицы: «... 01.DOC», «Извещение.pdf». Расширение может быть в любом
# регистре — площадка отдаёт и «01.DOC», и «izveshchenie.docx».
_FILE_NAME_PATTERN = re.compile(r"\.[A-Za-z0-9]{2,5}(\s|$)")

DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# На выборке при разработке встретились только эти два значения тега этапа — прочие останутся
# неразмеченными, а не будут угаданы (тот же принцип, что и в остальных адаптерах).
_STAGE_TO_STATUS = {
    "подача заявок": "collecting_bids",
    "завершено": "completed",
}


def _parse_status(stage_text: str | None) -> str | None:
    if not stage_text:
        return None
    return _STAGE_TO_STATUS.get(stage_text.strip().lower())


def _find_date_row_value(card, label_substring: str) -> str | None:
    for row in card.select(".purchase-date__row"):
        label = element_text(row.select_one(".purchase-date__label"))
        if label and label_substring in label:
            return element_text(row.select_one(".purchase-date__value"))
    return None


def _parse_card_documents(html: str) -> list[DocumentRef]:
    """Документы из таблицы документации карточки.

    Имя файла ищется по расширению, а не по номеру ячейки: в строке таблицы шесть колонок
    («Ид файла», «Дата создания», «Имя файла», «Описание», «Ссылка», «Сохранить»), часть из
    них пуста, и «первая непустая» легко оказывается заголовком секции — так в первом
    прогоне вместо «Документация о закупке в электронной форме 01.DOC» получилось «Файлы
    документации». Без верного имени не определить формат, а значит, и не извлечь текст.
    """

    soup = BeautifulSoup(html, "lxml")
    documents: list[DocumentRef] = []
    seen: set[str] = set()

    for table in soup.select(DOC_TABLE_SELECTOR):
        for row in table.select("tr"):
            link = row.select_one("a[href*='DownloadFile'], a[href*='DownLoadFile']")
            if link is None:
                continue
            href = (link.get("href") or "").strip()
            if not href:
                continue
            url = urljoin(BASE_URL, href)
            if url in seen:
                continue

            cells = [cell.get_text(strip=True) for cell in row.select("td")]
            name = _pick_file_name(cells)
            if name is None:
                continue

            seen.add(url)
            documents.append(DocumentRef(file_name=name, url=url))

    return documents


def _pick_file_name(cells: list[str]) -> str | None:
    candidates = [text for text in cells if text and text.lower() != "сохранить"]
    if not candidates:
        return None

    with_extension = [text for text in candidates if _FILE_NAME_PATTERN.search(text)]
    if with_extension:
        return with_extension[0]
    # Расширения в имени нет (встречается у документов, выложенных без него) — берём самую
    # содержательную ячейку: формат потом определится по сигнатуре файла.
    return max(candidates, key=len)


class SberbankAstAdapter(SourceAdapter):
    source_key = "sberbank_ast"

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

        Раньше забиралась только первая страница из двадцати — по наблюдаемому запросу
        пагинация показывала 17 страниц, то есть терялось около 95% найденного. Пагинация
        клиентская (Element UI), состояние в URL не отражается, поэтому листаем кликом по
        кнопке «вперёд» в той же сессии; признак последней страницы — её атрибут `disabled`."""

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
                page.goto(f"{BASE_URL}{LIST_PATH}", wait_until="networkidle", timeout=30000)
                page.fill(SEARCH_INPUT_SELECTOR, keyword)
                page.keyboard.press("Enter")
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(1500)  # список обновляется после AJAX-запроса

                for _ in range(max_pages):
                    try:
                        html = page.content()
                    except Exception:
                        page.wait_for_timeout(1500)
                        html = page.content()
                    yield html

                    next_button = page.query_selector(NEXT_PAGE_SELECTOR)
                    if next_button is None or next_button.get_attribute("disabled") is not None:
                        break

                    first_code = page.text_content(CARD_CODE_SELECTOR)
                    next_button.click()
                    try:
                        # Карточки перерисовываются на месте, поэтому ждём смены номера
                        # первой процедуры, а не просто затишья в сети.
                        page.wait_for_function(
                            "code => document.querySelector('a.purchase-card__purchcode')"
                            "?.textContent?.trim() !== code",
                            arg=(first_code or "").strip(),
                            timeout=15000,
                        )
                    except Exception:
                        break  # страница не сменилась — считаем выдачу исчерпанной
                    page.wait_for_timeout(500)
            finally:
                browser.close()

    def _parse_card(self, card, errors: list[PollError]) -> TenderSummary | None:
        link = card.select_one("a.purchase-card__purchcode")
        if link is None:
            return None
        external_id = element_text(link)
        if not external_id:
            return None
        external_id = external_id.lstrip("№").strip()

        try:
            detail_href = link.get("href", "")
            source_url = urljoin(BASE_URL, detail_href) if detail_href else f"{BASE_URL}{LIST_PATH}"

            deadline_date = parse_ru_date(_find_date_row_value(card, "Подача заявок по"))
            application_end = (
                datetime(deadline_date.year, deadline_date.month, deadline_date.day)
                if deadline_date
                else None
            )
            publish_date = parse_ru_date(_find_date_row_value(card, "Опубликовано"))

            # `div#PurchaseStageTerm`, а не просто `#PurchaseStageTerm`: площадка (баг вёрстки
            # на её стороне) дублирует один и тот же id на внешнем <span>-контейнере тултипа
            # ("Этап проведения процедуры") и на внутреннем <div> с самим значением ("Подача
            # заявок") — без тега-квалификатора `select_one` находил бы внешний и склеивал текст
            # подсказки со значением.
            status_el = card.select_one("div#PurchaseStageTerm")

            # `.amount-item__value` тоже не работает: у этого <span> на площадке (тот же класс
            # бракованной вёрстки) атрибут `class` записан как `lass` — сам селектор по классу
            # никогда не совпадёт. Берём текст родительской обёртки `.amount-item__wrapper`
            # (у неё атрибут class валиден), внутри нет ничего другого, кроме суммы.
            price_text = element_text(card.select_one(".amount-item__wrapper"))

            return TenderSummary(
                external_id=external_id,
                title=element_text(card.select_one(".purchase-object__content")) or "(без наименования)",
                source_url=source_url,
                customer_name=element_text(card.select_one(".purchase-organizer__content")),
                status=_parse_status(element_text(status_el)),
                price=parse_price(price_text),
                currency="RUB",
                publish_date=publish_date,
                application_end=application_end,
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одной карточки не должна прервать разбор
            errors.append(PollError(external_id, f"Не удалось разобрать карточку: {exc}"))
            return None

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}

        for keyword in self.search_keywords:
            found_any = False
            try:
                for html in self._search_pages(keyword):
                    cards = BeautifulSoup(html, "lxml").select("div.purchase-card")
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
        raise ValueError(f"Тендер {external_id} не найден в выдаче Сбербанк-АСТ")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Файлы документации с карточки закупки, а если их нет — из ЕИС по номеру.

        Карточка на `utp.sberbank-ast.ru` — ASP.NET-страница, но блок документации она
        дорисовывает скриптом уже после загрузки: в исходном HTML таблицы файлов нет вовсе,
        поэтому здесь нужен браузер (тот же Playwright, что и для реестра площадки).

        Ссылки «Сохранить» есть и у справочных памяток площадки («Онлайн-помощь»), поэтому
        берём строки только из таблицы документации закупки — `PurchaseDocumentationInfo_*`.
        Имя файла лежит отдельной ячейкой: в самой ссылке только идентификатор `fid`.
        """

        card_url = source_url or self.get_tender_details(external_id).source_url
        documents: list[DocumentRef] = []
        try:
            documents = self._fetch_card_documents(card_url)
        except Exception as exc:  # noqa: BLE001 - карточка не открылась: остаётся путь через ЕИС
            logger.warning(
                f"Документы закупки {external_id} не получены с карточки Сбербанк-АСТ: {exc}"
            )

        return documents or fetch_eis_documents(external_id)

    def _fetch_card_documents(self, card_url: str) -> list[DocumentRef]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
                page.goto(card_url, wait_until="networkidle", timeout=40000)
                # Таблица документации подставляется отдельным запросом уже после
                # `networkidle`, поэтому ждём именно её, а не общего состояния страницы.
                try:
                    page.wait_for_selector(DOC_TABLE_SELECTOR, timeout=15000)
                except Exception:  # noqa: BLE001 - у закупки может не быть документов вовсе
                    return []
                html = page.content()
            finally:
                browser.close()

        return _parse_card_documents(html)
