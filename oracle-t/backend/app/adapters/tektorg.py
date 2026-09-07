"""Адаптер ТЭК-Торг (tektorg.ru) — раздел 4.1 (источник №5), 5.1 ТЗ.

Поиск процедур (`/procedures?name=...`) — Next.js-приложение (Pages Router) на styled-
components: карточки результатов, как и на Фабрикант (`app/adapters/fabrikant.py`), подгружаются
клиентским кодом, а не присутствуют в исходном серверном HTML. У площадки нашёлся также
`/_next/data/<buildId>/ru/procedures.json?name=...` — но `<buildId>` меняется при каждом
редеплое площадки, поэтому URL не воспроизводится напрямую (httpx), а получается через
Playwright: открывает страницу поиска с ключевым словом в query-параметре и разбирает
отрисованную HTML-разметку.

Верстка не использует смысловые (BEM) классы — только автосгенерированные styled-components
(`sc-<hash>-<n>`), которые тоже могут смениться при переразвёртывании фронтенда площадки. Более
устойчивой альтернативы не нашлось (нет ни `data-*` атрибутов, ни семантических классов), поэтому
подписи полей карточки ("Организатор", "Начальная цена" и т.д.) ищутся по их видимому тексту, а
не по классу — это переживёт смену хеша класса, но не смену самого текста подписи на площадке.
Класс карточки-контейнера (`sc-6c01eeae-0`) — общий для всех разновидностей карточек (закупки,
продажа имущества), несмотря на разную внутреннюю раскладку полей.
"""

from __future__ import annotations

import json
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
from app.adapters.parsing_utils import element_text, parse_price

BASE_URL = "https://www.tektorg.ru"
SEARCH_PATH = "/procedures"
CARD_SELECTOR = "div.sc-6c01eeae-0"
ID_SELECTOR = ".sc-375e6608-4"
TITLE_LINK_SELECTOR = ".sc-6c01eeae-7"
STATUS_SELECTOR = ".sc-3e697cd2-2"
CATEGORY_SELECTOR = ".sc-375e6608-6 span"

# Защитный предел обхода страниц выдачи — тот же принцип, что и в остальных адаптерах
# (см. app/adapters/eis.py): не уходить в бесконечный цикл, если площадка перестанет
# отдавать признак конца выдачи.
MAX_SEARCH_PAGES = 20

DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# На площадке в одной выдаче лежат и закупки, и торги по продаже имущества — они различаются
# только разделом в ссылке (`/sale/` против `/44-fz/`, `/223-fz/`, `/market/`, `/org/<…>/`).
# Поиск по ключевым словам стабильно подмешивает вторые: «Продажа бывш. АЗС (сарай-склад,
# счетчик электрической энергии…)» содержит нужные слова в описании имущества, но это
# продажа объекта, а не закупка приборов. Для тендерного отдела (раздел 3 ТЗ) это шум,
# причём заметный — в наблюдаемой выдаче таких карточек была четверть.
_EXCLUDED_URL_SECTIONS = ("/sale/",)

# Формулировки статуса собраны по выборке при разработке (сортировка «По актуальности» на самом
# сайте отдавала все эти статусы на первой странице выдачи). Нераспознанная формулировка статус
# не угадывает, а оставляет пустым — тот же принцип, что и в остальных адаптерах.
_STATUS_TO_STATUS = {
    "приём заявок": "collecting_bids",
    "прием заявок": "collecting_bids",
    "работа комиссии": "evaluation",
    "архив": "completed",
    "отменён": "cancelled",
    "отменена": "cancelled",
}


def _parse_status(text: str | None) -> str | None:
    if not text:
        return None
    return _STATUS_TO_STATUS.get(text.strip().lower())


def _field_value(card, label: str):
    """Сосед подписи поля по её видимому тексту (см. докстринг модуля — площадка не даёт более
    устойчивой зацепки, чем сам текст подписи). Возвращает сам тег-сосед, а не его текст, — для
    полей с датой (`<time datetime="...">`) вызывающий код читает атрибут `datetime`, а не текст."""

    for label_el in card.find_all("div"):
        if element_text(label_el) == label:
            return label_el.find_next_sibling()
    return None


def _field_text(card, label: str) -> str | None:
    return element_text(_field_value(card, label))


def _field_datetime(card, label: str) -> datetime | None:
    value_el = _field_value(card, label)
    if value_el is None or value_el.name != "time":
        return None
    raw = value_el.get("datetime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _document_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=40.0,
        verify=resolve_verify(BASE_URL),
        follow_redirects=True,
    )


def _parse_next_data_documents(html: str) -> list[DocumentRef]:
    """Документы процедуры из состояния Next.js на странице карточки."""

    soup = BeautifulSoup(html, "lxml")
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None or not script.string:
        return []

    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return []

    procedure = (
        payload.get("props", {}).get("pageProps", {}).get("procedureItem")
    ) or {}

    raw_documents: list[dict] = list(procedure.get("documents") or [])
    for protocol in procedure.get("protocols") or []:
        raw_documents.extend(protocol.get("documents") or [])

    documents: list[DocumentRef] = []
    seen: set[str] = set()
    for item in raw_documents:
        url = (item.get("httpLink") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        documents.append(
            DocumentRef(
                file_name=item.get("filename") or "Документ (ТЭК-Торг)",
                url=url,
            )
        )
    return documents


class TektorgAdapter(SourceAdapter):
    source_key = "tektorg"

    def __init__(self, *, search_keywords: list[str] | None = None) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS

    def _search_html(self, keyword: str, page_number: int = 1) -> str:
        url = f"{BASE_URL}{SEARCH_PATH}?name={quote(keyword)}"
        if page_number > 1:
            url = f"{url}&page={page_number}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=30000)
                try:
                    page.wait_for_selector(CARD_SELECTOR, timeout=10000)
                except Exception:
                    pass  # по этому ключевому слову может не быть результатов — не фатально
                return page.content()
            finally:
                browser.close()

    def _search_pages(self, keyword: str):
        """Страницы выдачи по ключевому слову. Площадка отдаёт по 15 карточек и не показывает
        общее число найденного, а адаптер раньше читал только первую страницу — то есть по
        каждому запросу терялось всё, что не поместилось в первые 15 позиций. Признак конца
        выдачи — пустая страница или повтор уже виденных карточек: за последней страницей
        площадка отдаёт её же содержимое, а не пустой список."""

        seen_ids: set[str] = set()
        for page_number in range(1, MAX_SEARCH_PAGES + 1):
            html = self._search_html(keyword, page_number)
            soup = BeautifulSoup(html, "lxml")
            cards = soup.select(CARD_SELECTOR)
            if not cards:
                break

            page_ids = {
                element_text(card.select_one(ID_SELECTOR)) or ""
                for card in cards
            }
            if page_ids and page_ids <= seen_ids:
                break
            seen_ids |= page_ids
            yield cards

    def _parse_card(self, card, errors: list[PollError]) -> TenderSummary | None:
        id_el = card.select_one(ID_SELECTOR)
        link = card.select_one(TITLE_LINK_SELECTOR)
        if id_el is None or link is None:
            return None
        external_id = element_text(id_el)
        if not external_id:
            return None
        external_id = external_id.lstrip("№").strip()

        try:
            href = link.get("href") or ""
            source_url = urljoin(BASE_URL, href) if href else f"{BASE_URL}{SEARCH_PATH}"

            if any(section in source_url for section in _EXCLUDED_URL_SECTIONS):
                return None  # торги по продаже имущества — не закупка, см. _EXCLUDED_URL_SECTIONS

            publish_dt = _field_datetime(card, "Дата публикации")

            return TenderSummary(
                external_id=external_id,
                title=element_text(link) or "(без наименования)",
                source_url=source_url,
                customer_name=_field_text(card, "Организатор"),
                organizer_name=_field_text(card, "Организатор"),
                procurement_method=element_text(card.select_one(CATEGORY_SELECTOR)),
                status=_parse_status(element_text(card.select_one(STATUS_SELECTOR))),
                price=parse_price(_field_text(card, "Начальная цена")),
                currency="RUB",
                publish_date=publish_dt.date() if publish_dt else None,
                application_end=_field_datetime(card, "Дата окончания приема заявок"),
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
                for cards in self._search_pages(keyword):
                    found_any = True
                    for card in cards:
                        summary = self._parse_card(card, outcome.errors)
                        if summary is not None:
                            seen[summary.external_id] = summary
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
        raise ValueError(f"Тендер {external_id} не найден в выдаче ТЭК-Торг")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Реальные файлы документации закупки.

        Сайт на Next.js, и всё состояние страницы лежит в её же HTML — в теге
        `<script id="__NEXT_DATA__">`. Там, в `procedureItem.documents`, у каждого документа
        есть читаемое имя и `httpLink` на открытое API площадки
        (`api.tektorg.ru/open-api/documents/...`), которое отдаёт файл без авторизации.

        Поэтому Playwright, нужный реестру (список процедур дорисовывается скриптом), здесь
        не требуется: достаточно забрать HTML карточки и прочитать из него JSON.

        Протоколы (`protocols[].documents`) берём тоже: в них публикуются итоги и разъяснения,
        которые для анализа требований не менее полезны, чем сама документация.
        """

        card_url = source_url or self.get_tender_details(external_id).source_url
        response = fetch_with_retry(
            _document_client(), "GET", card_url, headers={"Accept": "text/html"}
        )
        response.raise_for_status()
        documents = _parse_next_data_documents(response.text)
        return documents or fetch_eis_documents(external_id)
