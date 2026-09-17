"""Поиск в интернете через Yandex Search API (правка по замечанию заказчика 15.09.2026).

Зачем справочнику поисковик. Обход каталога на сайте производителя видит только то, что
выложено в каталоге. Новое исполнение прибора туда попадает с опозданием — а документация
на него на том же сайте уже лежит: у Нартиса руководство на НАРТИС-И100 с корпусом W115
находится по адресу `nartis.ru/upload/iblock/…pdf`, но ни одна страница каталога на него не
ссылается. Поисковик такие документы индексирует, и запрос вида
`site:nartis.ru НАРТИС-И100 W115 руководство по эксплуатации` возвращает его первой строкой
(проверено вживую 15.09.2026).

Учётные данные — те же, что у YandexGPT (`yandex_ai_studio_settings`): Search API живёт в
том же облаке и принимает тот же API-ключ и Folder ID. Отдельной настройки в админ-панели
не нужно — если ключу не выдана роль `search-api.executor`, сервис ответит 403, и это
попадёт в журнал как понятное сообщение, а не как падение.

Модуль намеренно ничего не знает о справочнике: только запрос → список документов выдачи.
Что искать и как отбирать — в `app/services/document_discovery.py`.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from loguru import logger

SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
REQUEST_TIMEOUT_SECONDS = 60.0
# Сколько документов просить у выдачи. Больше десятка не нужно: годный документ либо в
# первых строках, либо его нет вовсе, а каждый запрос платный.
DEFAULT_GROUPS_ON_PAGE = 10


class YandexSearchError(RuntimeError):
    """Поиск не выполнен: сеть, права ключа, формат ответа. Вызывающий код решает, считать
    ли это сбоем шага или штатным «не нашлось»."""


@dataclass
class SearchHit:
    url: str
    title: str = ""
    domain: str = ""
    mime_type: str = ""
    # Фрагменты текста документа с подсветкой запроса — по ним видно, о том ли приборе
    # документ, не скачивая его.
    passages: list[str] = field(default_factory=list)
    position: int = 0

    @property
    def host(self) -> str:
        return (self.domain or urlparse(self.url).netloc).lower()

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.mime_type.lower() or urlparse(self.url).path.lower().endswith(".pdf")

    @property
    def text(self) -> str:
        """Заголовок и фрагменты одной строкой — для проверки упоминания модели."""

        return " ".join([self.title, *self.passages])


class YandexSearchClient:
    def __init__(self, api_key: str, folder_id: str, *, user_agent: str = "Mozilla/5.0") -> None:
        self._api_key = api_key
        self._folder_id = folder_id
        self._user_agent = user_agent

    def search(self, query: str, *, groups: int = DEFAULT_GROUPS_ON_PAGE) -> list[SearchHit]:
        """Синхронный поиск по русскому индексу. Пустой список — ничего не нашлось;
        `YandexSearchError` — поиск не удался."""

        body = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query,
                "familyMode": "FAMILY_MODE_NONE",
                "page": "0",
            },
            "groupSpec": {
                "groupMode": "GROUP_MODE_FLAT",
                "groupsOnPage": str(groups),
                "docsInGroup": "1",
            },
            "maxPassages": "3",
            "region": "225",
            "l10N": "LOCALIZATION_RU",
            "folderId": self._folder_id,
            "responseFormat": "FORMAT_XML",
            "userAgent": self._user_agent,
        }
        try:
            response = httpx.post(
                SEARCH_URL,
                headers={"Authorization": f"Api-Key {self._api_key}"},
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - сеть; вызывающий код логирует
            raise YandexSearchError(f"Yandex Search API недоступен: {exc}") from exc

        if response.status_code == 403:
            raise YandexSearchError(
                "Yandex Search API отказал в доступе (403): у API-ключа из «Настройки → "
                "Интеграции» нет роли search-api.executor на каталоге"
            )
        if response.status_code >= 400:
            raise YandexSearchError(
                f"Yandex Search API вернул {response.status_code}: {response.text[:300]}"
            )
        try:
            raw = base64.b64decode(response.json()["rawData"])
        except (ValueError, KeyError, TypeError) as exc:
            raise YandexSearchError(f"Yandex Search API вернул неожиданный ответ: {exc}") from exc
        return parse_search_xml(raw)


def parse_search_xml(raw: bytes | str) -> list[SearchHit]:
    """Разбор XML-выдачи (`yandexsearch/response/results/grouping/group/doc`)."""

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise YandexSearchError(f"XML выдачи не разобран: {exc}") from exc

    error = root.find("./response/error")
    if error is not None and (error.text or "").strip():
        # Код 15 — «ничего не найдено», это не ошибка.
        if error.get("code") == "15":
            return []
        raise YandexSearchError(f"Поиск вернул ошибку: {error.text.strip()}")

    hits: list[SearchHit] = []
    for position, doc in enumerate(root.iter("doc"), start=1):
        url = (doc.findtext("url") or "").strip()
        if not url:
            continue
        hits.append(
            SearchHit(
                url=url,
                title=_flatten(doc.find("title")),
                domain=(doc.findtext("domain") or "").strip(),
                mime_type=(doc.findtext("mime-type") or "").strip(),
                passages=[_flatten(p) for p in doc.iter("passage")],
                position=position,
            )
        )
    return hits


def _flatten(element: ET.Element | None) -> str:
    """Текст элемента вместе с текстом вложенных `<hlword>` (подсветка запроса)."""

    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def search_with_credentials(db, query: str, *, groups: int = DEFAULT_GROUPS_ON_PAGE) -> list[SearchHit]:
    """Поиск с учётными данными из БД. Ошибка настройки или поиска — в лог и пустой список:
    отсутствие поисковика не должно ронять пополнение справочника, ему есть чем заняться и
    без него (обход сайта, ФГИС)."""

    from app.services.yandex_ai_client import YandexAiNotConfiguredError, get_credentials

    try:
        api_key, folder_id = get_credentials(db)
    except YandexAiNotConfiguredError as exc:
        logger.warning(f"Поиск в интернете пропущен: {exc}")
        return []
    try:
        return YandexSearchClient(api_key, folder_id).search(query, groups=groups)
    except YandexSearchError as exc:
        logger.warning(f"Поиск «{query}» не выполнен: {exc}")
        return []
