"""Адаптер Lot-online / ЭТП РАД (gz.lot-online.ru) — раздел 4.1 (источник №4), 5.1 ТЗ.

Список закупок (`/etp_front/procedure/list`) построен на Angular и сначала выглядел как
типичный SPA-кейс для Playwright — как и у ЭТП ГПБ (`app/adapters/etpgpb.py`) и в отличие от
Фабрикант/ТЭК-Торг (`app/adapters/fabrikant.py`, `app/adapters/tektorg.py`, которым Playwright
всё же понадобился). Но при наблюдении за сетевыми запросами страницы поиска нашёлся публичный
JSON API, которым эта страница сама и пользуется: `GET /etp_back/procedure/list` с ExtJS-подобным
синтаксисом фильтров/сортировки в query-параметрах (`filter[N][property]`, `filter[N][value]`, …).
Ответ — обычный JSON без обёртки JSON:API, без авторизации и без антибот-препятствий при
проверке. Playwright не нужен: `httpx` напрямую к этому API даёт те же данные, что видит
пользователь в браузере.

Страница карточки закупки — `/etp_front/procedure/view/procedure/common/<purchaseNumber>` (та
же ссылка, что и в интерфейсе площадки, просто без служебного `backUrl`, который нужен только
для кнопки «Назад»).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

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

BASE_URL = "https://gz.lot-online.ru"
API_PATH = "/etp_back/procedure/list"
PROCEDURE_PATH = "/etp_front/procedure/view/procedure/common/{number}"

# Поиск площадки (`filter[condition]=match`) требует совпадения всех слов запроса и **не
# приводит их к начальной форме**: «прибор учета электрической энергии» находит 0 закупок,
# а «приборов учета электрической энергии» — 425. Из-за этого исходная пара фраз давала
# всего 4 тендера на весь источник. Набор ниже перечисляет и падежные формы, и более
# короткие запросы; вместе они дают около 160 уникальных закупок вместо четырёх.
# Дубли между ключевыми словами отсеиваются дедупликацией по `external_id` в
# `list_new_tenders`, так что пересечения запросов безвредны.
DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "счетчиков электрической энергии",
    "прибор учета электрической энергии",
    "приборов учета электрической энергии",
    "счетчик электроэнергии",
    "счетчики электроэнергии",
    "прибор учета",
]

# `status` из API → внутренний статус (раздел 7 ТЗ). Площадка отдаёт статус по-разному в
# зависимости от раздела: у регулируемых закупок (44-ФЗ/223-ФЗ) — короткий код ("accept"),
# у коммерческих закупок (маркетплейс) — произвольный русский текст ("Идет прием заявок") без
# кода вовсе. Короткие коды собраны по вкладкам фильтра статуса на самом сайте («Прием заявок»,
# «Работа комиссии», «Заключение контракта», «Контракты заключены», «Контракт не может быть
# заключен», «Не состоялась», «Отменена») — сайт группирует «Заключение контракта» вместе с
# «Прием заявок»/«Работа комиссии» как ещё не завершённую закупку, поэтому и здесь она размечена
# как `evaluation`, а не `completed`. Текстовые статусы собраны по выборке при разработке —
# нераспознанный текст статус не угадывает, а оставляет пустым, как и в остальных адаптерах.
_STATUS_TO_STATUS = {
    "accept": "collecting_bids",
    "commission": "evaluation",
    "contract": "evaluation",
    "contracts_finished": "completed",
    "contract_finished": "completed",
    "contract_failed": "cancelled",
    "no_condition": "cancelled",
    "canceled": "cancelled",
    "идет прием заявок": "collecting_bids",
    "работа комиссии": "evaluation",
    "подписан протокол рассмотрения первых частей заявок": "evaluation",
    "подписан протокол рассмотрения вторых частей заявок": "evaluation",
    "завершен прием коммерческих предложений": "evaluation",
    "подписан итоговый протокол": "completed",
    "размещение завершено": "completed",
    "отменен прием коммерческих предложений": "cancelled",
    "опубликовано извещение об отказе от проведения": "cancelled",
    "опубликовано извещение об отказе от проведения закупки": "cancelled",
}


def _parse_amount(value) -> Decimal | None:
    if value is None:
        return None
    cleaned = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _parse_status(status: str | None) -> str | None:
    if not status:
        return None
    return _STATUS_TO_STATUS.get(status.strip().lower())


def _clean_name(value: str | None) -> str | None:
    # У части записей `customerFullName`/`placerFullName` приходит не пустой строкой, а
    # буквально строкой из двух кавычек (`""`) — похоже на артефакт того, как площадка
    # сериализует отсутствующее значение для этого поля на своей стороне. Такое значение не
    # пустое (`or` его не отфильтрует), но и не имя — заменяем на `None`, чтобы фолбэк на
    # организатора сработал так же, как при действительно пустом значении.
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped and stripped != '""' else None


class LotOnlineAdapter(SourceAdapter):
    source_key = "lot_online"

    def __init__(self, *, search_keywords: list[str] | None = None, page_size: int = 100) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS
        self.page_size = page_size

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
            verify=resolve_verify(BASE_URL),
            follow_redirects=True,
        )

    @staticmethod
    def _search_params(keyword: str, *, limit: int, offset: int) -> dict:
        return {
            "filter[0][condition]": "match",
            "filter[0][property]": "*",
            "filter[0][value]": keyword,
            # Без явной сортировки по дате порядок между последовательными запросами не
            # гарантирован (тот же риск нестабильной "релевантности", что и у ЭТП ГПБ, см.
            # app/adapters/etpgpb.py) — сортируем по дате публикации, самой свежей сначала.
            "sort[0][property]": "publicationDateTime",
            "sort[0][direction]": "DESC",
            "limit": limit,
            "offset": offset,
        }

    def _parse_item(self, item: dict, errors: list[PollError]) -> TenderSummary | None:
        external_id = item.get("purchaseNumber") or item.get("purchaseNumberCustom")
        if not external_id:
            errors.append(PollError(None, "Не найден номер закупки (purchaseNumber) в ответе API"))
            return None

        try:
            publish_dt = _parse_dt(item.get("publicationDateTime"))

            return TenderSummary(
                external_id=external_id,
                title=item.get("purchaseObjectInfo") or "(без наименования)",
                source_url=f"{BASE_URL}{PROCEDURE_PATH.format(number=external_id)}",
                customer_name=_clean_name(item.get("customerFullName"))
                or _clean_name(item.get("placerFullName")),
                organizer_name=_clean_name(item.get("placerFullName")),
                # `typeName` — готовая человекочитаемая подпись ("223-ФЗ / Запрос цен..."), но
                # заполнена не у всех разновидностей процедур на площадке (у регулируемых закупок
                # по 44-ФЗ/223-ФЗ она пустая) — тогда используем код `direction` ("44fz") как менее
                # красивый, но всегда присутствующий запасной вариант.
                procurement_method=item.get("typeName") or item.get("direction"),
                status=_parse_status(item.get("status")),
                price=_parse_amount(item.get("maxSum")),
                currency=item.get("currencyCode") or "RUB",
                publish_date=publish_dt.date() if publish_dt else None,
                application_end=_parse_dt(item.get("requestEndGiveDateTime")),
            )
        except Exception as exc:  # noqa: BLE001 - ошибка одной записи не должна прервать разбор
            errors.append(PollError(external_id, f"Не удалось разобрать запись: {exc}"))
            return None

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}
        max_pages = 30  # защитный предел на ключевое слово — см. app/adapters/eis.py

        with self._client() as client:
            for keyword in self.search_keywords:
                # Изоляция по ключевому слову — тот же принцип, что и в остальных адаптерах:
                # частичный результат полезнее, чем потеря всей выдачи источника из-за одного
                # неудачного запроса (раздел 5.9 ТЗ).
                try:
                    offset = 0
                    page = 0
                    while page < max_pages:
                        response = fetch_with_retry(
                            client,
                            "GET",
                            API_PATH,
                            params=self._search_params(keyword, limit=self.page_size, offset=offset),
                        )
                        response.raise_for_status()
                        payload = response.json().get("data") or {}
                        items = payload.get("items") or []
                        if not items:
                            break

                        for item in items:
                            summary = self._parse_item(item, outcome.errors)
                            if summary is not None:
                                seen[summary.external_id] = summary

                        total_count = payload.get("count", offset + len(items))
                        offset += len(items)
                        page += 1
                        if offset >= total_count:
                            break
                except Exception as exc:  # noqa: BLE001 - см. комментарий выше
                    outcome.errors.append(
                        PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                    )

        outcome.tenders = list(seen.values())
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        outcome = self.list_new_tenders(since=None)
        for summary in outcome.tenders:
            if summary.external_id == external_id:
                return TenderDetails(**summary.__dict__)
        raise ValueError(f"Тендер {external_id} не найден в выдаче Lot-online")

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Документация закупки из ЕИС по её реестровому номеру.

        Своего перечня файлов у площадки взять неоткуда: карточка процедуры не показывает файлы анонимному посетителю.
        Но закупки 44-ФЗ и 223-ФЗ публикуются в ЕИС по закону, и там документация открыта —
        см. `app/adapters/eis_documents.py`. Для коммерческих закупок площадки (без номера
        ЕИС) документов не будет: их публикуют только в личном кабинете.
        """

        return fetch_eis_documents(external_id)
