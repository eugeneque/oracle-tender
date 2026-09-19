"""Адаптер ЭТП ГПБ (etpgpb.ru) — раздел 4.1 (источник №6), 5.1 ТЗ.

Сайт построен на Nuxt (Vue) и сначала выглядел как типичный SPA-кейс для Playwright — список
процедур не приходит в исходном серверном HTML (`/procedures/` отдаёт только каркас страницы,
данные подгружаются клиентским JS). Но при наблюдении за сетевыми запросами реальной страницы
поиска нашёлся публичный JSON API, которым эта страница сама и пользуется:
`GET /api/v2/procedures/?search=<текст>&page=<N>&per=<M>` — обычный JSON:API-подобный ответ
(`data[]`, `meta.total_pages`), без авторизации и без антибот-препятствий при проверке.
Playwright не нужен: `httpx` напрямую к этому API даёт те же данные, что видит пользователь
в браузере, но быстрее и надёжнее HTML/JS-рендеринга.

`platform_url` в ответе API уже абсолютный (иногда — на другой поддомен, например
`gos.etpgpb.ru` или `etp.gpb.ru`, в зависимости от типа процедуры) — используется как есть.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

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
from bs4 import BeautifulSoup

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify

BASE_URL = "https://etpgpb.ru"
API_PATH = "/api/v2/procedures/"

DEFAULT_SEARCH_KEYWORDS = [
    "счетчик электрической энергии",
    "прибор учета электрической энергии",
]

# `stage` из API → внутренний статус (раздел 7 ТЗ). На выборке при разработке встретились
# только эти три значения — прочие останутся неразмеченными, а не будут угаданы.
_STAGE_TO_STATUS = {
    "accepting": "collecting_bids",
    "commission": "evaluation",
    "completed": "completed",
}


def _parse_document_items(html: str) -> list[DocumentRef]:
    """Документы из блока «Документация» страницы процедуры.

    Имя файла — первая строка текста блока (там же лежат дата публикации и метки ЭЦП, которые
    в имя попадать не должны). Ссылка внутри блока повторяется трижды (иконка, название,
    кнопка) — берётся первая, дубли отсеиваются по URL.
    """

    soup = BeautifulSoup(html, "lxml")
    documents: list[DocumentRef] = []
    seen: set[str] = set()

    for block in soup.select("div.procedureDocItem"):
        link = block.select_one("a[href]")
        if link is None:
            continue
        url = link.get("href", "").strip()
        if not url or url in seen:
            continue
        seen.add(url)

        strings = [text.strip() for text in block.stripped_strings if text.strip()]
        file_name = strings[0] if strings else "Документ (ЭТП ГПБ)"
        documents.append(DocumentRef(file_name=file_name, url=url))

    return documents


def _parse_amount(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class EtpgpbAdapter(SourceAdapter):
    source_key = "etpgpb"

    def __init__(self, *, search_keywords: list[str] | None = None, per_page: int = 100) -> None:
        self.search_keywords = search_keywords or DEFAULT_SEARCH_KEYWORDS
        self.per_page = per_page

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
            verify=resolve_verify(BASE_URL),
            follow_redirects=True,
        )

    def _parse_item(self, item: dict, errors: list[PollError]) -> TenderSummary | None:
        attrs = item.get("attributes", {})
        # Реестровый номер сохраняем отдельно от идентификатора: ниже он может быть заменён
        # на внутренний `id` записи, а для дедупликации между площадками нужен именно номер.
        registry_number = attrs.get("registry_number") or None
        external_id = registry_number
        if not external_id:
            # У части закупок реестрового номера нет вовсе — это не сбой разбора. Так
            # приходят, например, процедуры ГК «Газпром» на собственной площадке
            # (`etpgaz.gazprombank.ru`): `registry_number` и `state` пустые, но запись
            # полноценная — с наименованием, суммой, сроком подачи и ссылкой. Раньше такие
            # закупки отбрасывались и попутно засоряли лог ошибками; идентификатором служит
            # `id` записи API, он стабилен между опросами и годится для дедупликации.
            item_id = item.get("id")
            if item_id:
                external_id = f"{self.source_key}-{item_id}"
            else:
                errors.append(
                    PollError(None, "В ответе API нет ни registry_number, ни id записи")
                )
                return None

        try:
            source_url = (
                attrs.get("platform_url")
                or urljoin(BASE_URL, attrs.get("rebranding_truncated_path") or attrs.get("truncated_path") or "")
                or f"{BASE_URL}{API_PATH}"
            )

            return TenderSummary(
                external_id=external_id,
                registry_number=registry_number,
                title=attrs.get("title") or "(без наименования)",
                source_url=source_url,
                customer_name=attrs.get("company_name"),
                organizer_name=attrs.get("company_name"),
                procurement_method=attrs.get("procedure_type_name"),
                status=_STAGE_TO_STATUS.get(attrs.get("stage")),
                price=_parse_amount(attrs.get("amount")),
                currency=attrs.get("currency_name") or "RUB",
                publish_date=(
                    _parse_iso_datetime(attrs.get("date_published")).date()
                    if attrs.get("date_published")
                    else None
                ),
                application_end=_parse_iso_datetime(attrs.get("end_registration")),
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
                # Изоляция по ключевому слову — тот же принцип, что и в остальных адаптерах
                # (раздел 5.9 ТЗ): обрыв на 12-й странице не должен обнулять первые одиннадцать.
                try:
                    page = 1
                    while page <= max_pages:
                        response = fetch_with_retry(
                            client,
                            "GET",
                            API_PATH,
                            params={
                                "search": keyword,
                                "page": page,
                                "per": self.per_page,
                                # Без явной сортировки API отдаёт "по релевантности", которая
                                # заметно "плавает" между последовательными запросами — при обходе
                                # 15+ страниц это давало нестабильные результаты (часть записей
                                # пропускалась/дублировалась между страницами). Сортировка по дате
                                # публикации — детерминированная.
                                "sort": "by_published_desc",
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()
                        items = payload.get("data") or []
                        if not items:
                            break

                        for item in items:
                            summary = self._parse_item(item, outcome.errors)
                            if summary is not None:
                                self._collect(seen, summary)

                        total_pages = (payload.get("meta") or {}).get("total_pages", page)
                        if page >= total_pages:
                            break
                        page += 1
                except Exception as exc:  # noqa: BLE001 - см. комментарий выше
                    outcome.errors.append(
                        PollError(None, f"Не удалось получить выдачу по '{keyword}': {exc}")
                    )

        outcome.tenders = list(seen.values())
        return outcome

    def _find_procedure(self, external_id: str) -> dict | None:
        """Запись API по номеру закупки — точечным поиском, а не обходом всей выдачи.

        Поле `search` того же API принимает реестровый номер и возвращает искомую процедуру
        первой (проверено на реальных номерах). Раньше карточка искалась перебором всех
        страниц по ключевым словам: это десятки запросов ради одной записи, а тендер, который
        уже выпал из выдачи по ключевым словам, не находился вовсе.
        """

        with self._client() as client:
            response = fetch_with_retry(
                client, "GET", API_PATH, params={"search": external_id, "per": 20}
            )
            response.raise_for_status()
            items = response.json().get("data") or []

        for item in items:
            attrs = item.get("attributes", {})
            if attrs.get("registry_number") == external_id:
                return item
        # Процедуры без реестрового номера мы сохраняем под ключом вида `etpgpb-<id>`
        # (см. `_parse_item`) — по нему и ищем.
        if external_id.startswith(f"{self.source_key}-"):
            wanted_id = external_id.split("-", 1)[1]
            for item in items:
                if str(item.get("id")) == wanted_id:
                    return item
        return None

    def get_tender_details(self, external_id: str) -> TenderDetails:
        item = self._find_procedure(external_id)
        if item is None:
            raise ValueError(f"Тендер {external_id} не найден в выдаче ЭТП ГПБ")

        errors: list[PollError] = []
        summary = self._parse_item(item, errors)
        if summary is None:
            raise ValueError(f"Не удалось разобрать карточку тендера {external_id}: {errors}")
        return TenderDetails(**summary.__dict__)

    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Реальные файлы документации со страницы процедуры.

        Страница процедуры на `etpgpb.ru` отдаётся сервером уже с разметкой (Nuxt SSR), и в
        блоке «Документация» лежат прямые ссылки на файлы вида
        `https://etp.gpb.ru/file/get/t/LotDocuments/id/<N>/name/<hash>`. Имя файла в самой
        ссылке — хеш, поэтому читаемое название берётся из текста блока: без него в карточке
        тендера будут неразличимые строки, а по расширению определяется формат разбора.

        Playwright не нужен: список документов есть в серверном HTML.
        """

        item = self._find_procedure(external_id)
        if item is None:
            raise ValueError(f"Тендер {external_id} не найден в выдаче ЭТП ГПБ")

        attrs = item.get("attributes", {})
        page_path = attrs.get("rebranding_truncated_path") or attrs.get("truncated_path")
        if not page_path:
            return []

        with self._client() as client:
            response = fetch_with_retry(
                client, "GET", page_path, headers={"Accept": "text/html"}
            )
            response.raise_for_status()
            html = response.text

        documents = _parse_document_items(html)
        # У закупок 44-ФЗ, которые проводятся на площадке, блок «Документация» на её странице
        # пуст — файлы лежат в ЕИС. Запасной путь возвращает их по реестровому номеру.
        return documents or fetch_eis_documents(external_id)
