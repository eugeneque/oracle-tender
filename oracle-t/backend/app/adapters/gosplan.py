"""Адаптер «ГосПлан» — REST API к данным ЕИС (https://v2.gosplan.info, решение 28.09.2026).

Госплан сам выкачивает ЕИС и отдаёт закупки 44-ФЗ и 223-ФЗ структурированным JSON: номер,
объект закупки, НМЦК, срок подачи, коды ОКПД2 и субъекта РФ. В системе это отдельный канал
сбора (переключатель «Стандартные ресурсы | Госплан» на странице тендеров), а не ещё одна
площадка — см. `SourceType.GOSPLAN`.

**Тарифы.** Бесплатный контур `v2test.gosplan.info` работает без ключа, но с лимитом 10
запросов в минуту на адрес; платный `v2.gosplan.info` требует ключ в заголовке `apikey`
(Kong key-auth). Ключ берётся из «Настройки → Доступы к площадкам» (источник «Госплан»,
пароль = ключ); нет ключа — идём в бесплатный контур и сами держим темп.

**Как искать.** Поиск по `object_info` — полнотекстовый со стеммингом, но слова фразы
объединяются через ИЛИ: «счетчиков электрической энергии» возвращает всё подряд про
электросети и электроплиты (проверено 28.09.2026). Поэтому фразы профиля релевантности сюда
не передаются: адаптер берёт закупки по коду ОКПД2 приборов учёта и по нескольким
одиночным словам, а отсев воды, газа и тепла делает профиль релевантности при сохранении —
так же, как для остальных площадок.

**Документы.** Госплан отдаёт только реквизиты извещения, файлов у него нет. Реестровый
номер тот же, что в ЕИС, поэтому карточка и документы берутся адаптером ЕИС.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.adapters.base import (
    DocumentRef,
    PollError,
    PollOutcome,
    SourceAdapter,
    TenderDetails,
    TenderSummary,
)
from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry
from app.services import okpd2_service

FREE_BASE_URL = "https://v2test.gosplan.info"
PAID_BASE_URL = "https://v2.gosplan.info"

# Бесплатный контур: 10 запросов в минуту. Семь секунд между запросами оставляют запас на
# проверку доступности и ручные запросы с того же адреса.
FREE_REQUEST_INTERVAL = 7.0
PAID_REQUEST_INTERVAL = 0.3

PAGE_SIZE = 100
# API не отдаёт дальше skip=1000 — это и есть предел глубины одного запроса.
MAX_SKIP = 1000

# Код ОКПД2 приборов учёта (26.51.63 — счётчики электроэнергии, газа, воды). Классификатор
# в API ищет по началу кода.
DEFAULT_CLASSIFIERS: tuple[str, ...] = ("26.51.63",)
# Одиночные слова: закупки монтажа, поверки и АСКУЭ часто идут под кодом работ (43.21,
# 71.20), и по ОКПД2 их не найти.
DEFAULT_WORDS: tuple[str, ...] = ("счетчик", "АСКУЭ", "АИИС")

# Первый опрос без `since` — глубина выборки. Госплан понимает сокращения вида «1m».
FIRST_POLL_DEPTH = "1m"

LAWS: tuple[str, ...] = ("fz44", "fz223")

# Этап закупки в ЕИС (поле `stage`) → статус тендера в системе.
_STAGE_TO_STATUS = {
    1: "collecting_bids",
    2: "evaluation",
    3: "completed",
    4: "cancelled",
}

# Самые частые способы определения поставщика. Незнакомый код не теряется — остаётся
# подпись закона, а сам код виден в карточке ЕИС.
_PURCHASE_TYPES = {
    "epNotificationEF2020": "Электронный аукцион",
    "epNotificationEZK2020": "Запрос котировок в электронной форме",
    "epNotificationEOK2020": "Открытый конкурс в электронной форме",
    "purchaseNotice": "Извещение о закупке",
    "purchaseNoticeAE": "Аукцион в электронной форме",
    "purchaseNoticeAE94": "Аукцион в электронной форме",
    "purchaseNoticeEP": "Закупка у единственного поставщика",
    "purchaseNoticeOK": "Открытый конкурс",
    "purchaseNoticeZK": "Запрос котировок",
    "purchaseNoticeZP": "Запрос предложений",
}

_EIS_SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={number}"
_EIS_223_URL = "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber={number}"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Госплан отдаёт время без зоны, в UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_price(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _region_code(value) -> str | None:
    if isinstance(value, int) and 1 <= value <= 99:
        return f"{value:02d}"
    return None


class GosplanAdapter(SourceAdapter):
    source_key = "gosplan"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        classifiers: tuple[str, ...] = DEFAULT_CLASSIFIERS,
        words: tuple[str, ...] = DEFAULT_WORDS,
        sleep=time.sleep,
    ) -> None:
        self.api_key = api_key
        self.classifiers = classifiers
        self.words = words
        self._sleep = sleep
        self._last_request_at = 0.0

    def use_credentials(self, credentials) -> None:
        """Ключ платного тарифа — пароль первой активной учётки источника «Госплан»."""

        for credential in credentials:
            if credential.password:
                self.api_key = credential.password.strip()
                return

    @property
    def base_url(self) -> str:
        return PAID_BASE_URL if self.api_key else FREE_BASE_URL

    @property
    def tariff(self) -> str:
        return "платный" if self.api_key else "бесплатный"

    def _client(self) -> httpx.Client:
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["apikey"] = self.api_key
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=60.0)

    def _throttle(self) -> None:
        interval = PAID_REQUEST_INTERVAL if self.api_key else FREE_REQUEST_INTERVAL
        wait = self._last_request_at + interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()

    def _get(self, client: httpx.Client, path: str, params: dict) -> list[dict]:
        self._throttle()
        response = fetch_with_retry(client, "GET", path, params=params, backoff_base=15.0)
        if response.status_code == 401:
            raise ValueError(
                "Госплан отклонил ключ API (401) — проверьте ключ в «Настройки → Доступы к "
                "площадкам» или удалите его, чтобы работать на бесплатном тарифе"
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Госплан вернул неожиданный ответ: {str(data)[:200]}")
        return data

    def _summary(self, law: str, item: dict) -> TenderSummary | None:
        number = str(item.get("purchase_number") or "").strip()
        if not number:
            return None
        is_223 = law == "fz223"
        deadline_key = "submission_close_at" if is_223 else "collecting_finished_at"
        published_at = _parse_datetime(item.get("published_at"))
        okpd2 = item.get("okpd2") or []
        purchase_type = item.get("purchase_type") or ""
        method = _PURCHASE_TYPES.get(purchase_type)
        law_label = "223-ФЗ" if is_223 else "44-ФЗ"
        return TenderSummary(
            external_id=number,
            registry_number=number,
            title=(item.get("object_info") or "").strip() or "(без наименования)",
            source_url=(_EIS_223_URL if is_223 else _EIS_SEARCH_URL).format(number=number),
            procurement_method=f"{method} ({law_label})" if method else law_label,
            status=_STAGE_TO_STATUS.get(item.get("stage")),
            price=_parse_price(item.get("max_price")),
            currency=item.get("currency_code") or "RUB",
            application_end=_parse_datetime(item.get(deadline_key)),
            publish_date=published_at.date() if published_at else None,
            okpd2_code=okpd2_service.pick_relevant(okpd2),
            region_code=_region_code(item.get("region")),
        )

    def _queries(self) -> list[dict]:
        return [{"classifier": code} for code in self.classifiers] + [
            {"object_info": word} for word in self.words
        ]

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        seen: dict[str, TenderSummary] = {}
        # Обновлённые с прошлого опроса, а не только опубликованные: у закупки меняются
        # этап и срок подачи. Сутки запаса — на расхождение часов и опрос, прерванный
        # на середине.
        window = (
            {"updated_after": (since - timedelta(days=1)).isoformat()}
            if since is not None
            else {"published_forpast": FIRST_POLL_DEPTH}
        )

        with self._client() as client:
            for law in LAWS:
                for query in self._queries():
                    label = f"{law} {next(iter(query.values()))}"
                    try:
                        skip = 0
                        while skip <= MAX_SKIP:
                            params = {
                                **query,
                                **window,
                                "limit": PAGE_SIZE,
                                "skip": skip,
                                "sort": "published_at_desc",
                            }
                            items = self._get(client, f"/{law}/purchases", params)
                            for item in items:
                                summary = self._summary(law, item)
                                if summary is not None:
                                    self._collect(seen, summary)
                            if len(items) < PAGE_SIZE:
                                break
                            skip += PAGE_SIZE
                    except Exception as exc:  # noqa: BLE001 - сбой одного запроса не обнуляет остальные
                        outcome.errors.append(
                            PollError(None, f"Госплан ({self.tariff} тариф), {label}: {exc}")
                        )

        outcome.tenders = list(seen.values())
        return outcome

    def get_tender_details(self, external_id: str) -> TenderDetails:
        from app.adapters.eis import EisAdapter

        return EisAdapter().get_tender_details(external_id)

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        from app.adapters.eis import EisAdapter

        return EisAdapter().download_documents(external_id, source_url)

