"""Адаптер Росэлторг (roseltorg.ru) — раздел 4.1 (источник №11), 5.1 ТЗ.

Несмотря на React-виджеты на сайте (`etp_react` в путях статики — избранные лоты, часть
хедера), сама выдача поиска (`/procedures/search`) — обычный серверный HTML: реального JS для
получения списка тендеров не требуется, `httpx` + `BeautifulSoup` достаточно (тот же случай,
что и с ЕИС/ЕЭТП, несмотря на первоначальные опасения по поводу SPA — см. заметку источника
до реализации). Поиск по ключевым словам — через `query_field` (обычный GET-параметр,
Drupal-бэкенд, судя по `data-drupal-selector` в разметке формы).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

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

BASE_URL = "https://www.roseltorg.ru"
SEARCH_PATH = "/procedures/search"

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

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}

        with self._client() as client:
            for keyword in self.search_keywords:
                # Площадка отвечает не всегда: с части сетей соединение обрывается ещё на
                # TLS-рукопожатии (`Connection reset by peer`, воспроизводится и через curl,
                # и в браузере — похоже на фильтрацию по адресу источника). Такой отказ —
                # штатно обрабатываемая ошибка источника, а не аварийное завершение опроса:
                # адаптер обязан вернуть `PollOutcome`, чтобы мониторинг доступности
                # (раздел 5.9 ТЗ) увидел причину, а не общее «источник упал».
                try:
                    response = fetch_with_retry(
                        client, "GET", SEARCH_PATH, params={"query_field": keyword}
                    )
                    response.raise_for_status()
                except Exception as exc:  # noqa: BLE001 - см. комментарий выше
                    outcome.errors.append(
                        PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                    )
                    continue

                soup = BeautifulSoup(response.text, "lxml")
                items = soup.select("div.search-results__item")
                if not items:
                    outcome.errors.append(
                        PollError(None, f"Результаты поиска не найдены по '{keyword}'")
                    )
                    continue
                for item in items:
                    summary = self._parse_item(item, outcome.errors)
                    if summary is not None:
                        self._collect(seen, summary)

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
        """Документация закупки из ЕИС по её реестровому номеру.

        Своего перечня файлов у площадки взять неоткуда: антибот-защита закрывает карточку для любого клиента, кроме браузера с сессией.
        Но закупки 44-ФЗ и 223-ФЗ публикуются в ЕИС по закону, и там документация открыта —
        см. `app/adapters/eis_documents.py`. Для коммерческих закупок площадки (без номера
        ЕИС) документов не будет: их публикуют только в личном кабинете.
        """

        return fetch_eis_documents(external_id)
