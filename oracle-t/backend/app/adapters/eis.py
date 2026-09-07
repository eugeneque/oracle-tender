"""Адаптер ЕИС (zakupki.gov.ru) — раздел 4.1 (источник №1), 5.1 ТЗ.

Открытый вопрос №2 ТЗ (раздел 11) — можно ли использовать открытый API ЕИС вместо HTML-
скрапинга поисковой выдачи: проверено при реализации, публичный экспорт открытых данных
(`/epz/opendata/main.html`, ранее также раздававшийся по FTP) недоступен — оба способа
доступа отдают 404 / DNS не резолвится. Поэтому используется HTML-скрапинг страницы
результатов расширенного поиска (`/epz/order/extendedsearch/results.html`), как и
предполагает раздел 6.2 ТЗ (httpx + BeautifulSoup — верстка страницы серверная, без JS).

TLS-особенность (важно для эксплуатации): сертификат `*.zakupki.gov.ru` подписан
государственным `Russian Trusted Sub CA` (Минцифры России), которого нет в стандартном
доверенном списке большинства окружений вне РФ. Решение — доверять для этого клиента
только сохранённой цепочке `app/certs/russian_trusted_ca.pem` (Sub CA + Root CA сервера,
снята напрямую с боевого TLS-хендшейка), а не отключать проверку сертификата
полностью. Этот же бандл нужен на сервере при боевом развёртывании (раздел 14 ТЗ).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

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

BASE_URL = "https://zakupki.gov.ru"
SEARCH_PATH = "/epz/order/extendedsearch/results.html"

# Фильтрация по ОКПД2 (решение №9 ТЗ, код 26.51.63.130 — приоритетный) в поисковой форме
# ЕИС не работает через простой параметр вида "код=значение": скрытые поля формы
# (`okpd2Ids`/`okpd2IdsCodes`) ожидают внутренний GUID категории, а не сам код ОКПД2 —
# на исследованиях перед реализацией передача одного лишь текстового кода молча игнорировалась
# площадкой (возвращалась нефильтрованная лента всех закупок). Резолвинг кода в GUID через
# справочник категорий ЕИС — отдельная доработка (см. TODO в `_search_params`); пока адаптер
# фильтрует релевантность через обычный полнотекстовый поиск по ключевым словам, который
# на практике даёт релевантную выдачу (проверено вручную).
DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# `.registry-entry__header-mid__title` в карточке результата поиска — это не название
# закупки, а название текущего этапа процедуры (площадка использует его как статус).
# Настоящее название объекта закупки лежит в блоке "Объект закупки"
# (`.registry-entry__body-block` с соответствующим `.registry-entry__body-title`).
_STAGE_TO_STATUS = {
    "подача заявок": "collecting_bids",
    "работа комиссии": "evaluation",
    "закупка завершена": "completed",
    "определение поставщика завершено": "completed",
    "закупка отменена": "cancelled",
}


def _body_value(card, label: str) -> str | None:
    """Значение блока карточки `.registry-entry__body-block` по заголовку (например,
    "Объект закупки" или "Заказчик") — блоки идут в фиксированном, но не гарантированном
    порядке, поэтому ищем по подписи, а не по индексу."""

    for block in card.select(".registry-entry__body-block"):
        title = element_text(block.select_one(".registry-entry__body-title"))
        if title and label in title:
            value = block.select_one(".registry-entry__body-href a") or block.select_one(
                ".registry-entry__body-value"
            )
            return element_text(value)
    return None


def _parse_status(stage_label: str | None) -> str | None:
    if not stage_label:
        return None
    return _STAGE_TO_STATUS.get(stage_label.strip().lower())


def _parse_updated_date(card) -> date | None:
    """Дата "Обновлено" карточки — по ней отсортирована выдача (`sortBy=UPDATE_DATE`
    в `_search_params`), поэтому именно она, а не "Размещено", определяет, когда можно
    прекратить пагинацию при инкрементальном опросе (см. `list_new_tenders`): тендер,
    размещённый давно, но недавно изменившийся (цена, срок подачи и т.д.), должен быть
    подхвачен, а не пропущен из-за старой даты размещения."""

    data_values = card.select(".data-block__value")
    if len(data_values) < 2:
        return None
    return parse_ru_date(element_text(data_values[1]))


class EisAdapter(SourceAdapter):
    """Адаптер ЕИС. Извещения по 44-ФЗ и 223-ФЗ одинаково присутствуют в выдаче
    `extendedsearch` — тип закупки виден в `procurement_method` (см. `_parse_card`)."""

    source_key = "eis"

    def __init__(
        self,
        *,
        search_keywords: list[str] | None = None,
        records_per_page: int = 50,
    ) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS
        self.records_per_page = records_per_page

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=30.0,
            verify=resolve_verify(BASE_URL),
            follow_redirects=True,
        )

    def _search_params(self, page: int, search_string: str) -> dict[str, str]:
        # TODO(Этап 12 или доработка): резолвить ОКПД2-код в GUID через справочник категорий
        # ЕИС (виден в форме поиска как `okpd2Ids`/`okpd2IdsCodes`) и фильтровать по нему —
        # точнее, чем полнотекстовый поиск. Пока используется `searchString` (см. модульный
        # докстринг и `DEFAULT_SEARCH_KEYWORDS`).
        return {
            "searchString": search_string,
            "morphology": "on",
            "pageNumber": str(page),
            "sortDirection": "false",
            "recordsPerPage": f"_{self.records_per_page}",
            "showLotsInfoHidden": "false",
            "sortBy": "UPDATE_DATE",
            "fz44": "on",
            "fz223": "on",
            "currencyIdGeneral": "-1",
        }

    def _parse_card(self, card, errors: list[PollError]) -> TenderSummary | None:
        number_link = card.select_one(".registry-entry__header-mid__number a")
        if number_link is None:
            errors.append(PollError(None, "Не найден номер закупки в карточке результата поиска"))
            return None

        external_id = re.sub(r"\s+", "", number_link.get_text(strip=True)).lstrip("№")
        if not external_id:
            errors.append(PollError(None, "Пустой номер закупки в карточке результата поиска"))
            return None

        detail_href = number_link.get("href", "")
        source_url = urljoin(BASE_URL, detail_href) if detail_href else f"{BASE_URL}{SEARCH_PATH}"

        try:
            title = _body_value(card, "Объект закупки") or "(без наименования)"
            status = _parse_status(element_text(card.select_one(".registry-entry__header-mid__title")))
            procurement_method = element_text(card.select_one(".registry-entry__header-top__title"))
            # 44-ФЗ карточки иногда не публикуют заказчика напрямую, только организацию,
            # осуществляющую размещение — тогда используем её как ближайший доступный аналог.
            customer_name = (
                _body_value(card, "Заказчик")
                or _body_value(card, "Организатор")
                or _body_value(card, "Организация, осуществляющая")
            )

            price_text = element_text(card.select_one(".price-block__value"))
            price = parse_price(price_text)

            data_values = card.select(".data-block__value")
            publish_date = parse_ru_date(element_text(data_values[0])) if len(data_values) > 0 else None
            application_end_text = None
            for label in card.select(".data-block__title"):
                if "Окончание" in (label.get_text(strip=True) or ""):
                    value = label.find_next_sibling(class_="data-block__value")
                    application_end_text = element_text(value)
                    break
            application_end_date = parse_ru_date(application_end_text)
            application_end = (
                datetime(application_end_date.year, application_end_date.month, application_end_date.day)
                if application_end_date
                else None
            )

            return TenderSummary(
                external_id=external_id,
                # В ЕИС идентификатор закупки и есть её реестровый номер — по нему та же
                # закупка узнаётся среди записей ЭТП (раздел 5.1 ТЗ).
                registry_number=external_id,
                title=title,
                source_url=source_url,
                customer_name=customer_name,
                procurement_method=procurement_method,
                status=status,
                price=price,
                currency="RUB",
                application_end=application_end,
                publish_date=publish_date,
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одной карточки не должна прервать опрос
            errors.append(PollError(external_id, f"Не удалось разобрать карточку: {exc}"))
            return None

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}
        max_pages = 20  # защитный предел, чтобы один сбой площадки не ушёл в бесконечный цикл

        with self._client() as client:
            for keyword in self.search_keywords:
                # Сбой на одном ключевом слове не должен обнулять уже собранное по другим:
                # раньше исключение уходило наружу, сервис опроса ловил его на уровне всего
                # источника (`poll_source`) и терял весь результат целиком.
                try:
                    page = 1
                    while page <= max_pages:
                        response = fetch_with_retry(
                            client, "GET", SEARCH_PATH, params=self._search_params(page, keyword)
                        )
                        response.raise_for_status()
                        soup = BeautifulSoup(response.text, "lxml")
                        cards = soup.select(".search-registry-entry-block")
                        if not cards:
                            break

                        stop = False
                        for card in cards:
                            if since is not None:
                                updated_date = _parse_updated_date(card)
                                if updated_date and updated_date < since.date():
                                    stop = True
                                    continue
                            summary = self._parse_card(card, outcome.errors)
                            if summary is None:
                                continue
                            seen[summary.external_id] = summary

                        if stop or len(cards) < self.records_per_page:
                            break
                        page += 1
                except Exception as exc:  # noqa: BLE001 - см. комментарий выше
                    outcome.errors.append(
                        PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                    )

        outcome.tenders = list(seen.values())
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        with self._client() as client:
            response = fetch_with_retry(
                client, "GET", SEARCH_PATH, params=self._search_params(1, external_id)
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            card = soup.select_one(".search-registry-entry-block")
            errors: list[PollError] = []
            summary = self._parse_card(card, errors) if card else None
            if summary is None:
                raise ValueError(f"Тендер {external_id} не найден в выдаче ЕИС")
            return TenderDetails(**summary.__dict__, raw_html=str(card))

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Реальный список документов извещения (раздел 5.2 ТЗ). У 44-ФЗ и 223-ФЗ разный
        адрес вкладки "Документы" (у 223-ФЗ дополнительно нужен `noticeGuid`, которого нет в
        одном только номере закупки) — поэтому URL не собирается по шаблону, а берётся из
        самой карточки тендера: обе разновидности используют одинаковый класс вкладок
        (`a.tabsNav__item`), просто ищем среди них ссылку на `documents.html`."""

        details = self.get_tender_details(external_id)

        with self._client() as client:
            detail_response = fetch_with_retry(client, "GET", details.source_url)
            detail_response.raise_for_status()
            detail_soup = BeautifulSoup(detail_response.text, "lxml")

            documents_link = None
            for tab in detail_soup.select("a.tabsNav__item"):
                href = tab.get("href", "")
                if "documents.html" in href:
                    documents_link = href
                    break
            if documents_link is None:
                return []

            documents_url = urljoin(BASE_URL, documents_link)
            docs_response = fetch_with_retry(client, "GET", documents_url)
            docs_response.raise_for_status()
            docs_soup = BeautifulSoup(docs_response.text, "lxml")

            # 44-ФЗ и 223-ФЗ верстают карточку документа по-разному (у 223-ФЗ, например, имя
            # файла лежит в `data-tooltip` с вложенным HTML, а не в атрибуте `title`, как у
            # 44-ФЗ) — вместо двух разных наборов селекторов ищем по общему для обеих
            # разновидностей признаку: прямая ссылка на скачивание файла всегда ведёт на
            # `.../filestore/public/...`.
            refs: list[DocumentRef] = []
            for link in docs_soup.select("a[href*='filestore/public']"):
                file_url = link.get("href", "")
                if not file_url:
                    continue
                file_name = link.get("title")
                if not file_name:
                    tooltip_html = link.get("data-tooltip")
                    if tooltip_html:
                        file_name = element_text(BeautifulSoup(tooltip_html, "lxml"))
                file_name = file_name or element_text(link) or "Документ"
                refs.append(DocumentRef(file_name=file_name, url=urljoin(BASE_URL, file_url)))
            return refs
