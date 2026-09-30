"""Адаптер «Тендерплан» — API сервиса поиска тендеров tenderplan.ru (решение 30.09.2026).

Отдельный канал сбора, как Госплан (`SourceType.TENDERPLAN`): на странице тендеров у него
своя кнопка на переключателе каналов, закупки не смешиваются со стандартными.

**Доступ.** Бесплатного контура нет: каждый запрос — с персональным токеном (PAT) в
заголовке `Authorization: Bearer …`. Токен выпускается в личном кабинете Тендерплана
(настройки пользователя → «Интеграции с сервисами» → Open API, метод
`/api/users/pat/create`) и заводится в «Настройки → Тендерплан» — хранится учёткой источника
(пароль = токен). Нужен именно персональный токен: «сервисный ключ» приложения даёт только
`resources:external`, а выдача по ключам и поиск требуют прав пользователя. Справочники
(`/api/tools/*`) открыты без токена. Документация API: https://tenderplan.ru/api/doc/
(OpenAPI внутри страницы, проверено 30.09.2026).

**Как собирать.** Основной путь — «ключи» аккаунта: сохранённые в Тендерплане поисковые
фильтры (слова, исключения, ОКПД2, типы закупок), которые сервис сам прогоняет по своей
базе. `GET /api/tenders/v2/getlist?type=0&page=N` без `id` отдаёт тендеры всех ключей сразу,
а `fromPublicationDateTime` делает сбор инкрементальным. Ключ настраивается в интерфейсе
Тендерплана по профилю релевантности — так охват правится там же, где его видно, а наш
профиль и модель отбора по-прежнему работают поверх собранного.

Если ключей в аккаунте нет, адаптер ищет сам: `POST /api/search/v2/list?set=actual` по коду
ОКПД2 приборов учёта и одиночным словам, как у Госплана. Окна «с даты» у поиска нет, поэтому
каждый опрос проходит выборку «на приёме заявок» целиком.

**Номер и документы.** Закупки ЕИС (44-ФЗ и 223-ФЗ) сохраняются под реестровым номером —
карточка и документация берутся адаптером ЕИС, как у Госплана. У коммерческих площадок
номера ЕИС нет: идентификатор записи — `tp-<ИД Тендерплана>`, а документы берутся из
полной модели тендера (`/api/tenders/get`), где у каждого файла прямая ссылка на площадку.
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
from app.adapters.eis_documents import normalize_eis_number
from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry

BASE_URL = "https://tenderplan.ru"
SOURCE_KEY = "tenderplan"

# Квоты API: 500 запросов в минуту, на список тендеров — 60 в минуту. Секунда между
# запросами держит нас под обеими.
REQUEST_INTERVAL = 1.0
# Страниц одной выдачи: при 20–50 закупках на странице это сотни строк на запрос, больше
# по приборам учёта на приёме заявок не бывает.
MAX_PAGES = 30
# Выдача по ключам за окно публикации может быть длиннее поиска «на приёме заявок».
MAX_KEY_PAGES = 100
# Первый сбор по ключам — закупки, опубликованные за последний месяц.
FIRST_POLL_DAYS = 30

DEFAULT_CLASSIFIERS: tuple[str, ...] = ("26.51.63",)
DEFAULT_WORDS: tuple[str, ...] = ("счетчик", "АСКУЭ", "АИИС")

# Площадки Тендерплана (`/api/tools/types/list`): 0 — 223-ФЗ, 1 — 44-ФЗ, остальные —
# коммерческие площадки и порталы.
_TYPE_223 = 0
_TYPE_44 = 1

# Статусы (`/api/tools/statuses/list`) → статус тендера в системе. «Исполняется» и
# «Исполнение завершено» — после подведения итогов, для нас это завершённая закупка.
_STATUS_MAP = {
    1: "collecting_bids",
    2: "evaluation",
    3: "completed",
    4: "cancelled",
    5: "cancelled",
    6: "completed",
    7: "completed",
}

_EIS_44_URL = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={number}"
_EIS_223_URL = "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber={number}"
_CARD_URL = BASE_URL + "/app?tender={id}"

_ID_PREFIX = "tp-"


class TenderplanNoTokenError(RuntimeError):
    pass


def _from_millis(value) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _parse_price(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _region_code(value) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return f"{number:02d}" if 1 <= number <= 99 else None


def tenderplan_id(external_id: str) -> str | None:
    """ИД Тендерплана из идентификатора записи коммерческой закупки."""

    return external_id[len(_ID_PREFIX):] if external_id.startswith(_ID_PREFIX) else None


class TenderplanAdapter(SourceAdapter):
    source_key = SOURCE_KEY

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
        self._placing_ways: dict[int, str] = {}
        self._types: dict[int, str] = {}

    def use_credentials(self, credentials) -> None:
        """Токен — пароль первой активной учётки источника «Тендерплан»."""

        for credential in credentials:
            if credential.password:
                self.api_key = credential.password.strip()
                return

    def _ensure_token(self) -> str:
        """Документы запрашиваются вне опроса — адаптер создаёт `get_adapter` без учёток,
        поэтому токен дочитывается из базы сам."""

        if not self.api_key:
            from app.db.session import SessionLocal
            from app.services.credentials_service import get_credentials_for_source

            with SessionLocal() as db:
                self.use_credentials(get_credentials_for_source(db, SOURCE_KEY))
        if not self.api_key:
            raise TenderplanNoTokenError(
                "Не задан токен Тендерплана — выпустите его в личном кабинете tenderplan.ru "
                "и сохраните в «Настройки → Тендерплан»"
            )
        return self.api_key

    def _client(self) -> httpx.Client:
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(base_url=BASE_URL, headers=headers, timeout=60.0)

    def _throttle(self) -> None:
        wait = self._last_request_at + REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()

    def _request(self, client: httpx.Client, method: str, path: str, **kwargs):
        self._throttle()
        response = fetch_with_retry(client, method, path, backoff_base=5.0, **kwargs)
        if response.status_code in (401, 403):
            raise ValueError(
                f"Тендерплан отклонил токен ({response.status_code}) — проверьте токен в "
                "«Настройки → Тендерплан» и права search/tenders у него"
            )
        response.raise_for_status()
        return response.json()

    def _load_dictionaries(self, client: httpx.Client) -> None:
        """Названия способов размещения и площадок — открытые справочники. Без них закупка
        сохраняется всё равно, просто без способа проведения."""

        try:
            ways = self._request(client, "GET", "/api/tools/placingways/list")
            self._placing_ways = {item["_id"]: item["name"] for item in ways if "_id" in item}
            types = self._request(client, "GET", "/api/tools/types/list")
            self._types = {item["_id"]: item["name"] for item in types if "_id" in item}
        except Exception:  # noqa: BLE001 - справочник не обязателен для сбора
            pass

    def _summary(self, item: dict) -> TenderSummary | None:
        tp_id = str(item.get("_id") or "").strip()
        number = str(item.get("number") or "").strip()
        if not tp_id and not number:
            return None
        platform = item.get("type")
        eis_number = (
            normalize_eis_number(number) if platform in (_TYPE_44, _TYPE_223) else None
        )
        if eis_number:
            external_id = eis_number
            url = (_EIS_44_URL if len(eis_number) == 19 else _EIS_223_URL).format(
                number=eis_number
            )
        else:
            if not tp_id:
                return None
            external_id = f"{_ID_PREFIX}{tp_id}"
            url = item.get("href") or _CARD_URL.format(id=tp_id)

        method = self._placing_ways.get(item.get("placingWay"))
        platform_name = self._types.get(platform)
        if method and platform_name:
            procurement_method = f"{method} ({platform_name})"
        else:
            procurement_method = method or platform_name

        customers = item.get("customers") or []
        customer = customers[0].get("name") if customers and isinstance(customers[0], dict) else None
        published_at = _from_millis(item.get("publicationDateTime"))
        okpd2 = item.get("okpd2") or []
        return TenderSummary(
            external_id=external_id,
            registry_number=eis_number or number or None,
            title=(item.get("orderName") or "").strip() or "(без наименования)",
            source_url=url,
            customer_name=customer,
            procurement_method=procurement_method,
            status=_STATUS_MAP.get(item.get("status")),
            price=_parse_price(item.get("maxPrice")),
            currency=item.get("currency") or "RUB",
            application_start=_from_millis(item.get("submissionStartDateTime")),
            application_end=_from_millis(item.get("submissionCloseDateTime")),
            publish_date=published_at.date() if published_at else None,
            okpd2_code=okpd2[0] if okpd2 else None,
            region_code=_region_code(item.get("region")),
        )

    def _queries(self) -> list[tuple[str, dict]]:
        by_code = [
            (code, {"classificators": [{"name": "okpd2", "value": [code]}]})
            for code in self.classifiers
        ]
        by_word = [(word, {"words": {"value": word}}) for word in self.words]
        return by_code + by_word

    def _account_keys(self, client: httpx.Client) -> list | None:
        """Ключи аккаунта. `None` — узнать не удалось (у токена нет права `keys:read`):
        тогда выдачу по ключам всё равно пробуем, а без неё переходим на свой поиск."""

        try:
            data = self._request(client, "GET", "/api/keys/getall")
        except Exception:  # noqa: BLE001 - право keys:read не обязательно
            return None
        if isinstance(data, dict):
            data = data.get("keys")
        return data if isinstance(data, list) else None

    def _collect_by_keys(
        self, client: httpx.Client, since: datetime | None, seen: dict[str, TenderSummary]
    ) -> None:
        # Сутки запаса — на расхождение часов и опрос, прерванный на середине.
        start = (
            since - timedelta(days=1)
            if since is not None
            else datetime.now(timezone.utc) - timedelta(days=FIRST_POLL_DAYS)
        )
        known: set[str] = set()
        for page in range(MAX_KEY_PAGES):
            data = self._request(
                client,
                "GET",
                "/api/tenders/v2/getlist",
                params={
                    "type": 0,
                    "page": page,
                    "fromPublicationDateTime": int(start.timestamp() * 1000),
                    "publicationDateTime": -1,
                },
            )
            items = (data or {}).get("tenders") or []
            fresh = [item for item in items if item.get("_id") not in known]
            if not fresh:
                return
            for item in fresh:
                known.add(item.get("_id"))
                summary = self._summary(item)
                if summary is not None:
                    self._collect(seen, summary)

    def _collect_by_search(
        self, client: httpx.Client, seen: dict[str, TenderSummary], outcome: PollOutcome
    ) -> None:
        for label, key in self._queries():
            try:
                known: set[str] = set()
                for page in range(MAX_PAGES):
                    data = self._request(
                        client,
                        "POST",
                        "/api/search/v2/list",
                        params={"set": "actual", "page": page},
                        json={"key": key},
                    )
                    items = (data or {}).get("tenders") or []
                    fresh = [item for item in items if item.get("_id") not in known]
                    if not fresh:
                        break
                    for item in fresh:
                        known.add(item.get("_id"))
                        summary = self._summary(item)
                        if summary is not None:
                            self._collect(seen, summary)
                    total = (data or {}).get("count")
                    if isinstance(total, (int, float)) and len(known) >= total:
                        break
            except Exception as exc:  # noqa: BLE001 - сбой одного запроса не обнуляет остальные
                outcome.errors.append(PollError(None, f"Тендерплан, «{label}»: {exc}"))

    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        outcome = PollOutcome()
        try:
            self._ensure_token()
        except TenderplanNoTokenError as exc:
            outcome.errors.append(PollError(None, str(exc)))
            return outcome

        seen: dict[str, TenderSummary] = {}
        with self._client() as client:
            self._load_dictionaries(client)
            keys = self._account_keys(client)
            by_keys_failed = False
            if keys != []:
                try:
                    self._collect_by_keys(client, since, seen)
                except Exception as exc:  # noqa: BLE001 - без выдачи по ключам остаётся поиск
                    by_keys_failed = True
                    if keys is not None:
                        # Ключи есть, а выдача по ним не пришла — это ошибка сбора, а не
                        # повод молча подменить охват своим поиском.
                        outcome.errors.append(
                            PollError(None, f"Тендерплан, выдача по ключам: {exc}")
                        )
            if keys == [] or (keys is None and by_keys_failed):
                self._collect_by_search(client, seen, outcome)

        outcome.tenders = list(seen.values())
        return outcome

    def _full_model(self, tp_id: str) -> dict:
        self._ensure_token()
        with self._client() as client:
            data = self._request(client, "GET", "/api/tenders/get", params={"id": tp_id})
        if not isinstance(data, dict):
            raise ValueError(f"Тендерплан вернул неожиданный ответ: {str(data)[:200]}")
        return data

    def get_tender_details(self, external_id: str) -> TenderDetails:
        tp_id = tenderplan_id(external_id)
        if tp_id is None:
            from app.adapters.eis import EisAdapter

            return EisAdapter().get_tender_details(external_id)

        data = self._full_model(tp_id)
        summary = self._summary({**data, "_id": data.get("_id") or tp_id})
        if summary is None:
            raise ValueError(f"Тендерплан не отдал тендер {tp_id}")
        return TenderDetails(**summary.__dict__)

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        tp_id = tenderplan_id(external_id)
        if tp_id is None:
            from app.adapters.eis import EisAdapter

            return EisAdapter().download_documents(external_id, source_url)

        refs: list[DocumentRef] = []
        for attachment in self._full_model(tp_id).get("attachments") or []:
            url = attachment.get("href")
            name = attachment.get("realName") or attachment.get("displayName")
            if not url or not name:
                continue
            refs.append(DocumentRef(file_name=name, url=url))
        return refs
