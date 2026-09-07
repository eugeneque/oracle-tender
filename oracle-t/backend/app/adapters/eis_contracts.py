"""Реестр контрактов ЕИС как источник истории участий МИРТЕК (разделы 5.5.1, 7 ТЗ).

Заменил синхронизацию с OPTI (решение 04.09.2026). Причина смены источника простая: у OPTI
нет ни публичного API, ни выдаваемых нам токенов — сессионный JWT из чужого браузера не
является доступом, на который можно опереть работающую функцию. Реестр контрактов ЕИС
открыт, ищется по ИНН поставщика и не требует ни учётной записи, ни ключа.

**Что этот источник даёт и, главное, чего не даёт.** В реестре контрактов лежат только
заключённые контракты — то есть исключительно НАШИ ПОБЕДЫ. Проигрышей и снятий с торгов
здесь нет и быть не может: их место — протоколы подведения итогов, отдельный источник.
Поэтому записи помечаются `wins_only`, и измерение History знает, что доля побед по такой
выборке равна 100% не потому, что мы не проигрывали, а потому, что проигрыши не попали в
данные (`ai_profile_service`). Считать по ней win-rate — то же самое, что считать ноль за
«нет данных», только в обратную сторону.

Фильтр по поставщику — параметр `supplierTitle`, куда подаётся ИНН. Проверено на боевой
выдаче: с ИНН запрос возвращает десятки записей конкретной компании, без него — весь реестр
(десятки миллионов). Соседний `supplierIdCode` площадкой игнорируется, на него не
переключаться.

TLS: тот же государственный `Russian Trusted Sub CA`, что у `eis.py` — цепочка берётся из
`app/certs/russian_trusted_ca.pem` через `resolve_verify` (см. модульный докстринг `eis.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify
from app.adapters.parsing_utils import element_text, parse_price, parse_ru_date

BASE_URL = "https://zakupki.gov.ru"
SEARCH_PATH = "/epz/contract/search/results.html"

RECORDS_PER_PAGE = 50
# Потолок на случай, если пагинация на стороне площадки зациклится: у одного поставщика
# десятки-сотни контрактов, тысячи страниц означали бы не полноту, а сбой.
MAX_PAGES = 40

_INN_RE = re.compile(r"^\d{10}(?:\d{2})?$")


class EisContractsError(RuntimeError):
    """Выгрузка не удалась. Текст показывается администратору как есть."""


@dataclass
class ParticipationRecord:
    """Одно участие, приведённое к нашим типам.

    `outcome` здесь всегда `won` — см. модульный докстринг. Поле оставлено явным, а не
    зашитым в вызывающем коде, чтобы следующий источник (протоколы итогов) встроился сюда же,
    не переписывая структуру.
    """

    external_tender_id: str | None
    tender_title: str | None
    customer_name: str | None
    final_contract_value: Decimal | None
    executed_at: date | None
    outcome: str = "won"
    source_url: str | None = None


def _body_value(card, label: str) -> str | None:
    """Значение блока карточки по подписи, а не по индексу: блоки идут в устойчивом, но не
    гарантированном порядке, и у контрактов по 44-ФЗ и 223-ФЗ состав блоков разный."""

    for block in card.select(".registry-entry__body-block"):
        title = element_text(block.select_one(".registry-entry__body-title"))
        if title and label in title:
            value = block.select_one(".registry-entry__body-value") or block.select_one(
                ".registry-entry__body-href"
            )
            return element_text(value)
    return None


def _lot_value(card, label: str) -> str | None:
    """Значение из блока сведений о лоте (`lots-wrap-content__body--item`).

    Отдельный разбор нужен потому, что «Объекты закупки» лежит не там же, где «Заказчик»:
    у карточки контракта две разные разметки блоков, и один селектор на обе не работает —
    наименование закупки молча приходило пустым.
    """

    for block in card.select(".lots-wrap-content__body--item"):
        title = element_text(block.select_one(".lots-wrap-content__body__title"))
        if title and label in title:
            return element_text(block.select_one(".lots-wrap-content__body__val"))
    return None


def _data_value(card, label: str) -> str | None:
    for block in card.select(".data-block"):
        title = element_text(block.select_one(".data-block__title"))
        if title and label in title:
            return element_text(block.select_one(".data-block__value"))
    return None


def _notice_number(card) -> str | None:
    """Реестровый номер ЗАКУПКИ, к которой относится контракт.

    Именно он, а не номер контракта, связывает участие с тендером в нашей базе: тендеры
    хранятся под номером извещения (`Tender.registry_number`). Ссылки на извещение в
    карточке может не быть — например, у закупок у единственного поставщика по ч. 12 ст. 93;
    тогда участие останется несвязанным, и это нормально.
    """

    for link in card.select("a[href]"):
        match = re.search(r"regNumber=(\d+)", link.get("href", ""))
        if match:
            return match.group(1)
    return None


def parse_card(card) -> ParticipationRecord | None:
    number_link = card.select_one(".registry-entry__header-mid__number a")
    contract_number = (
        re.sub(r"\s+", "", number_link.get_text(strip=True)).lstrip("№") if number_link else None
    )
    # Ключ дедупликации: номер извещения, если он есть, иначе реестровый номер контракта.
    # Без хоть какого-то идентификатора запись сохранять нельзя — повторная выгрузка создала
    # бы её заново.
    external_id = _notice_number(card) or contract_number
    if not external_id:
        return None

    price = parse_price(element_text(card.select_one(".price-block__value")))
    return ParticipationRecord(
        external_tender_id=external_id,
        tender_title=_lot_value(card, "Объекты закупки") or _body_value(card, "Объекты закупки"),
        customer_name=_body_value(card, "Заказчик"),
        final_contract_value=price,
        executed_at=parse_ru_date(_data_value(card, "Заключение контракта")),
        source_url=(
            f"{BASE_URL}/epz/contract/contractCard/common-info.html"
            f"?reestrNumber={contract_number}"
            if contract_number
            else None
        ),
    )


class EisContractsAdapter:
    """Выгрузка контрактов компании из реестра ЕИС по её ИНН."""

    def __init__(self, *, records_per_page: int = RECORDS_PER_PAGE) -> None:
        self.records_per_page = records_per_page

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=40.0,
            verify=resolve_verify(BASE_URL),
            follow_redirects=True,
        )

    def _params(self, inn: str, page: int) -> dict[str, str]:
        return {
            # Поле «Поставщик (подрядчик, исполнитель)» расширенного поиска. ИНН здесь
            # работает как точный отбор, в отличие от `searchString`, который ищет по всему
            # тексту и притаскивает чужие контракты, где наше название — просто товарный знак.
            "supplierTitle": inn,
            "morphology": "on",
            "pageNumber": str(page),
            "sortDirection": "false",
            "recordsPerPage": f"_{self.records_per_page}",
            "sortBy": "UPDATE_DATE",
            "fz44": "on",
            "fz223": "on",
            "currencyIdGeneral": "-1",
        }

    def fetch_participations(self, inn: str) -> list[ParticipationRecord]:
        inn = (inn or "").strip()
        if not _INN_RE.match(inn):
            raise EisContractsError(
                f"«{inn}» не похож на ИНН: ожидается 10 цифр у организации или 12 у "
                "индивидуального предпринимателя. Проверьте поле в профиле компании."
            )

        records: list[ParticipationRecord] = []
        seen: set[str] = set()
        with self._client() as client:
            for page in range(1, MAX_PAGES + 1):
                try:
                    response = fetch_with_retry(
                        client, "GET", SEARCH_PATH, params=self._params(inn, page)
                    )
                except httpx.HTTPError as exc:
                    raise EisContractsError(
                        f"Не удалось обратиться к реестру контрактов ЕИС: {exc}"
                    ) from exc
                if response.status_code >= 400:
                    raise EisContractsError(
                        f"Реестр контрактов ЕИС ответил HTTP {response.status_code}."
                    )

                cards = BeautifulSoup(response.text, "lxml").select(
                    ".search-registry-entry-block"
                )
                if not cards:
                    break

                page_records = 0
                for card in cards:
                    record = parse_card(card)
                    # Битая карточка не должна обрывать выгрузку остальных (раздел 5.9 ТЗ).
                    if record is None or record.external_tender_id in seen:
                        continue
                    seen.add(record.external_tender_id)
                    records.append(record)
                    page_records += 1

                if len(cards) < self.records_per_page:
                    break
                if page_records == 0:
                    # Страница целиком из уже виденных записей — признак того, что площадка
                    # перестала листать и повторяет одну и ту же выдачу.
                    break

        logger.info(f"Из реестра контрактов ЕИС по ИНН {inn} получено записей: {len(records)}")
        return records
