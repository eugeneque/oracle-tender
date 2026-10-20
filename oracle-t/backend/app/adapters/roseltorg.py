"""Адаптер Росэлторг (roseltorg.ru) — раздел 4.1 (источник №11), 5.1 ТЗ.

Несмотря на React-виджеты на сайте (`etp_react` в путях статики — избранные лоты, часть
хедера), сама выдача поиска (`/procedures/search`) — обычный серверный HTML: реального JS для
получения списка тендеров не требуется, `httpx` + `BeautifulSoup` достаточно (тот же случай,
что и с ЕИС/ЕЭТП, несмотря на первоначальные опасения по поводу SPA — см. заметку источника
до реализации). Поиск по ключевым словам — через `query_field` (обычный GET-параметр,
Drupal-бэкенд, судя по `data-drupal-selector` в разметке формы).

Корпоративные секции (Россети, РусГидро, Ростелеком, Интер РАО — 20.10.2026) — тот же поиск
с фильтром `source[]=<номер секции>`, см. `RoseltorgSectionAdapter` ниже. У самих секций
(`rosseti.roseltorg.ru` и т.п.) список процедур отдаётся только после входа (RPC
`Procedure.list` отвечает `no_session`), а Интер РАО пускает лишь через «Росэлторг.ID» с
Яндекс-капчей — публичная витрина отдаёт те же закупки без учётной записи.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.adapters.eis_documents import fetch_eis_documents, normalize_eis_number
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

BASE_URL = "https://www.roseltorg.ru"
SEARCH_PATH = "/procedures/search"

# Сколько страниц выдачи (по 10 карточек, нумерация с 0) читать на одну фразу. До 20.10.2026
# читалась только первая — и её почти целиком занимал Росатом: закупки Россетей и РусГидро
# по той же фразе оставались на второй-третьей странице и в базу не попадали.
MAX_PAGES = 3

DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# `.search-results__status` несёт модификатор класса `status__icon--<состояние>`.
# В выборке при разработке встретился только `acceptance` (идёт приём заявок) — остальные
# модификаторы (например `finished`) в наблюдаемых карточках означали категорию/льготу
# участника (МСП и т.п.), а не стадию закупки, поэтому не размечены, чтобы не угадывать.
_STATUS_MODIFIER_TO_STATUS = {
    "acceptance": "collecting_bids",
}


class RoseltorgAdapter(SourceAdapter):
    source_key = "roseltorg"
    # Номера секций для фильтра `source[]`; пусто — вся витрина площадки.
    section_ids: tuple[str, ...] = ()
    max_pages: int = MAX_PAGES

    def __init__(self, *, search_keywords: list[str] | None = None) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=30.0,
            verify=resolve_verify(BASE_URL),
            follow_redirects=True,
        )

    @staticmethod
    def _parse_status(item) -> str | None:
        status_el = item.select_one(".search-results__status")
        if status_el is None:
            return None
        classes = status_el.get("class") or []
        for cls in classes:
            if cls.startswith("status__icon--"):
                modifier = cls.removeprefix("status__icon--")
                return _STATUS_MODIFIER_TO_STATUS.get(modifier)
        return None

    def _parse_item(self, item, errors: list[PollError]) -> TenderSummary | None:
        external_id = item.get("data-feature-favorite-lots-procedure-number")
        if not external_id:
            errors.append(PollError(None, "Не найден номер закупки в карточке результата поиска"))
            return None

        try:
            link = item.select_one(".search-results__subject a")
            title = element_text(link) or "(без наименования)"
            detail_href = link.get("href", "") if link else ""
            source_url = urljoin(BASE_URL, detail_href) if detail_href else f"{BASE_URL}{SEARCH_PATH}"

            customer_name = element_text(item.select_one(".search-results__customer a"))
            procurement_method = element_text(item.select_one(".search-results__type"))
            price = parse_price(element_text(item.select_one(".search-results__sum p.desktop")))
            deadline_date = parse_ru_date(
                element_text(item.select_one(".search-results__timing time.search-results__time"))
            )
            application_end = (
                datetime(deadline_date.year, deadline_date.month, deadline_date.day)
                if deadline_date
                else None
            )

            return TenderSummary(
                external_id=external_id,
                title=title,
                source_url=source_url,
                customer_name=customer_name,
                procurement_method=procurement_method,
                status=self._parse_status(item),
                price=price,
                currency="RUB",
                application_end=application_end,
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одной карточки не должна прервать разбор
            errors.append(PollError(external_id, f"Не удалось разобрать карточку: {exc}"))
            return None

    def _search_params(self, keyword: str, page: int) -> dict:
        params: dict = {"query_field": keyword}
        if self.section_ids:
            params["source[]"] = list(self.section_ids)
        if page:
            params["page"] = page
        return params

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}

        with self._client() as client:
            for keyword in self.search_keywords:
                for page in range(self.max_pages):
                    # Площадка отвечает не всегда: с части сетей соединение обрывается ещё на
                    # TLS-рукопожатии (`Connection reset by peer`, воспроизводится и через curl,
                    # и в браузере — похоже на фильтрацию по адресу источника). Такой отказ —
                    # штатно обрабатываемая ошибка источника, а не аварийное завершение опроса:
                    # адаптер обязан вернуть `PollOutcome`, чтобы мониторинг доступности
                    # (раздел 5.9 ТЗ) увидел причину, а не общее «источник упал».
                    try:
                        response = fetch_with_retry(
                            client, "GET", SEARCH_PATH, params=self._search_params(keyword, page)
                        )
                        response.raise_for_status()
                    except Exception as exc:  # noqa: BLE001 - см. комментарий выше
                        outcome.errors.append(
                            PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                        )
                        break

                    soup = BeautifulSoup(response.text, "lxml")
                    items = soup.select("div.search-results__item")
                    if not items:
                        # В секции пустая выдача по фразе — норма (у РусГидро нет закупок
                        # «поверки счётчиков» каждую неделю); на всей витрине пустая первая
                        # страница означает сменившуюся разметку.
                        if page == 0 and not self.section_ids:
                            outcome.errors.append(
                                PollError(None, f"Результаты поиска не найдены по '{keyword}'")
                            )
                        break
                    before = len(seen)
                    for item in items:
                        summary = self._parse_item(item, outcome.errors)
                        if summary is not None:
                            self._collect(seen, summary)
                    # Страница без единой новой закупки — конец выдачи (площадка на номер
                    # за пределами отдаёт последнюю страницу) или повтор: дальше не идём.
                    if len(seen) == before or len(items) < 10:
                        break

        outcome.tenders = list(seen.values())
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        outcome = self.list_new_tenders(since=None)
        for summary in outcome.tenders:
            if summary.external_id == external_id:
                return TenderDetails(**summary.__dict__)
        raise ValueError(f"Тендер {external_id} не найден в выдаче Росэлторг")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Документация закупки — с карточки площадки, запасной путь — ЕИС.

        До 25.09.2026 документы брались только из ЕИС по реестровому номеру, и коммерческие
        закупки площадки (номера `RH…` — ДЭК, сетевые компании Россетей) оставались без
        единого файла: в ЕИС их нет. На карточке же файлы лежат обычными ссылками в блоке
        «Документы» (`a.lot-docs__file`) на `msp.roseltorg.ru/api/v1/documents/<uuid>`,
        и скачиваются без входа — вход нужен только архиву «Скачать все документы».

        Сбой карточки поднимается наружу, только если в ЕИС идти не с чем: иначе анализ
        написал бы «документов нет» там, где их просто не удалось получить.
        """

        card_url = source_url or f"{BASE_URL}/procedure/{external_id}"
        card_error: Exception | None = None
        try:
            with self._client() as client:
                response = fetch_with_retry(
                    client, "GET", card_url, headers={"Accept": "text/html"}
                )
                response.raise_for_status()
            documents = parse_card_documents(response.text)
            if documents:
                return documents
        except Exception as exc:  # noqa: BLE001 - дальше запасной путь через ЕИС
            card_error = exc

        if card_error is not None and normalize_eis_number(external_id) is None:
            raise RuntimeError(f"карточка Росэлторга не получена: {card_error}") from card_error
        return fetch_eis_documents(external_id)


class RoseltorgSectionAdapter(RoseltorgAdapter):
    """Корпоративная секция Росэлторга — отдельный источник в «Настройки → Источники».

    Секции заведены отдельными площадками (20.10.2026), а не только частью общей витрины:
    по ним МИРТЕК работает с заказчиками напрямую, и закупки Россетей или РусГидро нужно
    видеть и фильтровать своим источником. Та же закупка, уже собранная общим «Росэлторгом»,
    не задваивается — она переходит к своей секции (`tender_service._upsert_tender`).
    """

    # Секция уже узкая — можно листать глубже, чем всю витрину.
    max_pages = 5


class RoseltorgRossetiAdapter(RoseltorgSectionAdapter):
    source_key = "roseltorg_rosseti"
    section_ids = ("18",)


class RoseltorgRushydroAdapter(RoseltorgSectionAdapter):
    source_key = "roseltorg_rushydro"
    section_ids = ("14",)


class RoseltorgRostelecomAdapter(RoseltorgSectionAdapter):
    source_key = "roseltorg_rt"
    section_ids = ("6",)


class RoseltorgInterraoAdapter(RoseltorgSectionAdapter):
    source_key = "roseltorg_interrao"
    # 29 — закупки ПАО «Интер РАО», 21 — его корпоративный интернет-магазин (мелкие
    # закупки товаров, в т.ч. приборов учёта).
    section_ids = ("29", "21")


def parse_card_documents(html: str) -> list[DocumentRef]:
    """Файлы из блока «Документы» карточки процедуры.

    Берутся все подблоки — «Приложения к извещению», «Протоколы», «Разъяснения»: в
    протоколах и разъяснениях тоже бывают требования. Имя файла — в `title` ссылки; у
    протоколов там название без расширения («Протокол подведения итогов»), и тип тогда
    виден только по классу `file-pdf` / `file-docx` — без расширения файл не разобрался бы.
    """

    soup = BeautifulSoup(html, "lxml")
    documents: list[DocumentRef] = []
    for link in soup.select("a.lot-docs__file[href]"):
        name = (link.get("title") or element_text(link) or "").strip()
        if not name:
            continue
        extension = next(
            (
                cls.removeprefix("file-")
                for cls in link.get("class") or []
                if cls.startswith("file-") and cls != "file-"
            ),
            None,
        )
        if extension and not Path(name).suffix:
            name = f"{name}.{extension}"
        documents.append(
            DocumentRef(
                file_name=name,
                url=urljoin(BASE_URL, link["href"]),
                file_type=f".{extension}" if extension else None,
            )
        )
    return documents
