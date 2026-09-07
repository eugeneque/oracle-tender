"""Итог конкретной закупки в ЕИС: кто получил контракт (разделы 5.5.1, 7 ТЗ).

Нужен для автоматического определения ПРОИГРЫШЕЙ. Прямого пути к ним нет, и это не наше
упущение, а устройство раскрытия информации:

* реестра протоколов с поиском по участнику в ЕИС не существует (проверено: `/epz/protocol/…`
  отвечает 404 на все варианты, поиска по участнику нет ни в одном открытом реестре);
* в самих протоколах электронных процедур участники **обезличены** — в таблице стоят
  идентификационные номера заявок вида `120100244`, а не ИНН. Раскрывается только победитель,
  после заключения контракта.

Поэтому «мы проиграли» не вычитывается из ЕИС напрямую. Зато вычисляется: если наш тендерный
отдел отметил закупку как «заявка подана» (`stage = application_submitted`), закупка
завершилась, а контракт получил не МИРТЕК — это проигрыш. Внешний факт (победитель) берётся
из ЕИС, факт нашего участия — из собственного пайплайна; ни то, ни другое не самоотчёт о
результате.

Тип процедуры (`ea20`, `ok20`, …) в адресе заранее неизвестен, поэтому он определяется по
редиректу с обобщённого `/epz/order/notice/view/common-info.html` — перебирать типы вслепую
значило бы делать десяток лишних запросов на каждую закупку.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify
from app.adapters.parsing_utils import parse_price

BASE_URL = "https://zakupki.gov.ru"
COMMON_INFO_PATH = "/epz/order/notice/view/common-info.html"
RESULTS_PATH = "/epz/order/notice/{kind}/view/supplier-results.html"

_KIND_RE = re.compile(r"/epz/order/notice/([^/]+)/view/")
_WINNER_HEADER = "Поставщик (подрядчик, исполнитель), с которым заключен контракт"


class EisResultsError(RuntimeError):
    """Итог закупки не удалось получить — вызывающий код считает это «исход неизвестен»."""


@dataclass
class PurchaseOutcome:
    """Итог закупки: кто получил контракт и по какой цене."""

    registry_number: str
    winner_name: str | None
    contract_value: Decimal | None
    contract_registry_number: str | None
    source_url: str | None


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=40.0,
        verify=resolve_verify(BASE_URL),
        follow_redirects=True,
    )


def _winner_row(soup: BeautifulSoup) -> list[str] | None:
    """Строка таблицы заключённых контрактов.

    Ищется по подписи столбца, а не по индексу таблицы: на странице их несколько
    (заказчики, предложения участников, контракты), и их порядок зависит от типа процедуры.
    """

    for table in soup.select("table"):
        headers = [
            re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
            for cell in table.select("th")
        ]
        if not any(_WINNER_HEADER in header for header in headers):
            continue
        index = next(i for i, header in enumerate(headers) if _WINNER_HEADER in header)
        for row in table.select("tr"):
            cells = [
                re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                for cell in row.select("td")
            ]
            if len(cells) > index and cells[index]:
                return cells
    return None


def fetch_outcome(registry_number: str) -> PurchaseOutcome:
    """Возвращает итог закупки по её реестровому номеру."""

    registry_number = (registry_number or "").strip()
    if not registry_number:
        raise EisResultsError("Пустой реестровый номер закупки.")

    try:
        with _client() as client:
            common = fetch_with_retry(
                client, "GET", COMMON_INFO_PATH, params={"regNumber": registry_number}
            )
            if common.status_code >= 400:
                raise EisResultsError(
                    f"ЕИС ответил HTTP {common.status_code} на карточку закупки "
                    f"{registry_number}."
                )
            match = _KIND_RE.search(str(common.url))
            if match is None:
                raise EisResultsError(
                    f"Не удалось определить тип процедуры закупки {registry_number}."
                )
            kind = match.group(1)
            path = RESULTS_PATH.format(kind=kind)
            results = fetch_with_retry(
                client, "GET", path, params={"regNumber": registry_number}
            )
            if results.status_code >= 400:
                raise EisResultsError(
                    f"ЕИС ответил HTTP {results.status_code} на результаты закупки "
                    f"{registry_number}."
                )
    except EisResultsError:
        raise
    except httpx.HTTPError as exc:
        logger.warning(f"Итог закупки {registry_number} не получен: {exc}")
        raise EisResultsError(f"Не удалось обратиться к ЕИС: {exc}") from exc

    soup = BeautifulSoup(results.text, "lxml")
    row = _winner_row(soup)
    if row is None:
        # Контракт ещё не заключён либо закупка не состоялась: исход неизвестен, и
        # придумывать его нельзя — вызывающий код оставит запись без исхода.
        return PurchaseOutcome(
            registry_number=registry_number,
            winner_name=None,
            contract_value=None,
            contract_registry_number=None,
            source_url=f"{BASE_URL}{path}?regNumber={registry_number}",
        )

    # Порядок столбцов зафиксирован формой ЕИС: реестровый номер, заказчик, поставщик, цена.
    contract_number = row[0] if row and re.fullmatch(r"\d{15,25}", row[0] or "") else None
    winner = next(
        (cell for cell in row if cell and not re.fullmatch(r"[\d\s.,]+", cell)),
        None,
    )
    winner_name = row[2] if len(row) > 2 and row[2] else winner
    price = next(
        (parse_price(cell) for cell in row if cell and re.search(r"\d[\d\s]*,\d{2}", cell)),
        None,
    )
    return PurchaseOutcome(
        registry_number=registry_number,
        winner_name=winner_name,
        contract_value=price,
        contract_registry_number=contract_number,
        source_url=f"{BASE_URL}{path}?regNumber={registry_number}",
    )
