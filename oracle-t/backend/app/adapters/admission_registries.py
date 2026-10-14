"""Выгрузки реестров допуска: ГИСП (реестр промышленной продукции, ПП РФ № 719) и перечень
оборудования, допущенного к применению на объектах ПАО «Россети» (замечание 13.10.2026, п.7:
«нет информации о реестрах допуска»).

**Что известно о реестрах.** Ни у одного нет открытого API. На странице реестра ГИСП есть
кнопки «Скачать перечень» и «Скачать только действующие» — выгрузка в XLSX; перечень
Россетей — PDF на сайте компании, адрес которого меняется с каждой редакцией («…на
16.09.2026.pdf»). Оба сайта роботам из-за рубежа отвечают 403 или страницей «Проверка
безопасности», поэтому с машины разработки адаптер проверить нельзя — только на сервере в
России.

**Как устроено.** Адаптер ничего не знает о каталоге: он скачивает выгрузку и превращает её
в список строк `RegistryRow` (номер, наименование продукции, организация, даты). Ссылка на
выгрузку ищется на странице реестра по подписи и расширению, а не зашита: адрес файла
меняется, подпись — нет. Если сайт выгрузку роботу не отдал, тот же разбор принимает файл,
загруженный человеком из браузера, — это гарантированный путь, и интерфейс предлагает его
сразу после неудачи.

Шапка XLSX не фиксирована (колонки у ГИСП переставлялись между редакциями выгрузки), поэтому
колонки узнаются по словам заголовка, а строка заголовка — по тому, что в ней нашёлся номер
записи и наименование.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify

# Страница реестра и перечня — с них начинается поиск выгрузки.
GISP_PP719_PAGE = "https://gisp.gov.ru/pp719v2/pub/prod/"
ROSSETI_ZAK_PAGE = "https://www.rosseti.ru/suppliers-and-contractors/attestation/"

REQUEST_TIMEOUT_SECONDS = 120.0
# Полная выгрузка ГИСП — десятки мегабайт; всё заметно крупнее — не та ссылка.
MAX_FILE_BYTES = 300 * 1024 * 1024


class RegistryUnavailable(Exception):
    """Выгрузку получить не удалось. Текст — для человека: что произошло и что делать."""


@dataclass
class RegistryRow:
    record_number: str | None
    product_name: str
    organization: str = ""
    okpd2: str = ""
    issued_at: date | None = None
    valid_to: date | None = None
    # Текст, в котором ищется обозначение модели: наименование и организация, у PDF —
    # строка перечня с соседними.
    raw: str = ""


@dataclass
class RegistryFile:
    content: bytes
    file_name: str
    url: str | None = None


# --------------------------------------------------------------------------- скачивание


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=resolve_verify(GISP_PP719_PAGE),
    )


def _get(client: httpx.Client, url: str) -> httpx.Response:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise RegistryUnavailable(f"{url} не ответил: {exc}") from exc
    if response.status_code == 403:
        raise RegistryUnavailable(
            f"{url} отказал роботу (403). Скачайте выгрузку в браузере и загрузите файл вручную."
        )
    if response.status_code >= 400:
        raise RegistryUnavailable(f"{url} ответил {response.status_code}")
    if len(response.content) > MAX_FILE_BYTES:
        raise RegistryUnavailable(f"{url}: файл больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
    return response


def find_download_link(html: str, base_url: str, *, extensions: tuple[str, ...], prefer: tuple[str, ...]) -> str | None:
    """Ссылка на выгрузку на странице: по расширению в адресе либо по подписи «скачать».
    Из нескольких — та, в подписи или адресе которой есть слово из `prefer` («действующ»)."""

    soup = BeautifulSoup(html, "lxml")
    candidates: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ").split()).lower()
        lowered = href.lower()
        is_file = any(ext in lowered for ext in extensions)
        is_download = "скачать" in text or "выгруз" in text or anchor.has_attr("download")
        if not (is_file or is_download):
            continue
        score = (2 if is_file else 0) + (1 if is_download else 0)
        if any(word in text or word in lowered for word in prefer):
            score += 3
        candidates.append((score, urljoin(base_url, href)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: -item[0])
    return candidates[0][1]


def download_gisp() -> RegistryFile:
    """Выгрузка действующих записей реестра ПП 719 со страницы ГИСП."""

    with _client() as client:
        page = _get(client, GISP_PP719_PAGE)
        content_type = page.headers.get("content-type", "")
        if "spreadsheet" in content_type or page.content[:2] == b"PK":
            return RegistryFile(page.content, "gisp_pp719.xlsx", GISP_PP719_PAGE)
        link = find_download_link(
            page.text, GISP_PP719_PAGE, extensions=(".xlsx", ".xls", "xls"), prefer=("действующ", "active")
        )
        if link is None:
            raise RegistryUnavailable(
                "На странице реестра ГИСП не нашлась ссылка на выгрузку (страница собирается "
                "скриптом в браузере). Скачайте «только действующие» в браузере и загрузите файл."
            )
        response = _get(client, link)
        if response.content[:2] != b"PK":
            raise RegistryUnavailable(f"По ссылке {link} пришла не таблица XLSX")
        return RegistryFile(response.content, link.rsplit("/", 1)[-1] or "gisp_pp719.xlsx", link)


def download_rosseti(known_url: str | None = None) -> RegistryFile:
    """PDF перечня Россетей (раздел II, вторичное оборудование). Ищется на странице
    аттестации по подписи; если не нашёлся — последний известный адрес."""

    with _client() as client:
        link: str | None = None
        try:
            page = _get(client, ROSSETI_ZAK_PAGE)
            link = find_download_link(
                page.text, ROSSETI_ZAK_PAGE, extensions=(".pdf",), prefer=("вторичн", "раздел ii", "раздел%20ii")
            )
        except RegistryUnavailable as exc:
            logger.info(f"Перечень Россетей: страница аттестации недоступна ({exc}), пробую известный адрес")
        link = link or known_url
        if not link:
            raise RegistryUnavailable("Адрес перечня ПАО «Россети» неизвестен — загрузите PDF вручную")
        response = _get(client, link)
        if not response.content.startswith(b"%PDF"):
            raise RegistryUnavailable(
                f"По адресу {link} пришёл не PDF (вероятно, страница «Проверка безопасности»). "
                "Скачайте перечень в браузере и загрузите файл."
            )
        return RegistryFile(response.content, link.rsplit("/", 1)[-1] or "rosseti.pdf", link)


# --------------------------------------------------------------------------- разбор XLSX

_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _to_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:
        # Дата Excel числом дней от 30.12.1899.
        return date(1899, 12, 30) + timedelta(days=int(value))
    match = _DATE_RE.search(str(value))
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _column_roles(header: list[str]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for index, raw in enumerate(header):
        text = (raw or "").lower().replace("ё", "е")
        if not text:
            continue
        if "номер" in text and ("реестр" in text or "запис" in text) and "number" not in roles:
            roles["number"] = index
        elif "окпд" in text and "okpd2" not in roles:
            roles["okpd2"] = index
        elif text.strip() == "инн" or text.startswith("инн "):
            roles.setdefault("inn", index)
        elif "наименование" in text and any(w in text for w in ("продукц", "товар", "изделия")):
            roles.setdefault("product", index)
        elif any(w in text for w in ("организац", "предприят", "производител", "изготовител", "юридическ")):
            roles.setdefault("organization", index)
        elif "дата" in text and any(w in text for w in ("внесен", "включ", "регистрац", "выдач")):
            roles.setdefault("issued_at", index)
        elif ("действ" in text and ("до" in text or "срок" in text or "окончан" in text)) or "окончани" in text:
            roles.setdefault("valid_to", index)
    return roles


def parse_xlsx(content: bytes, *, relevant: re.Pattern[str]) -> tuple[list[RegistryRow], int]:
    """Строки выгрузки ГИСП. Возвращаются только подходящие под `relevant` (счётчики
    электроэнергии: по ОКПД2 26.51.63 или по наименованию) и общее число строк."""

    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - битый файл → понятная ошибка, а не трассировка
        raise RegistryUnavailable(f"Файл не читается как таблица XLSX: {exc}") from exc

    rows: list[RegistryRow] = []
    total = 0
    for sheet in workbook.worksheets:
        roles: dict[str, int] | None = None
        for values in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in values]
            if roles is None:
                candidate = _column_roles(cells)
                if "number" in candidate and "product" in candidate:
                    roles = candidate
                continue
            if not any(cells):
                continue
            total += 1

            def cell(role: str, raw: bool = False):
                index = roles.get(role)
                if index is None or index >= len(values):
                    return None if raw else ""
                return values[index] if raw else cells[index]

            product = cell("product")
            okpd2 = cell("okpd2")
            if not (relevant.search(okpd2) or relevant.search(product)):
                continue
            rows.append(
                RegistryRow(
                    record_number=cell("number") or None,
                    product_name=product,
                    organization=cell("organization"),
                    okpd2=okpd2,
                    issued_at=_to_date(cell("issued_at", raw=True)),
                    valid_to=_to_date(cell("valid_to", raw=True)),
                    raw=" ".join(part for part in (product, cell("organization")) if part),
                )
            )
    workbook.close()
    if total == 0:
        raise RegistryUnavailable(
            "В таблице не нашлось строки заголовка с номером реестровой записи и наименованием "
            "продукции — это не выгрузка реестра ПП 719?"
        )
    return rows, total


# --------------------------------------------------------------------------- разбор PDF

_ZAK_NUMBER_RE = re.compile(r"(?:ЗАК|заключени\w*)[^№\n]{0,30}№\s*([\w./-]+)", re.IGNORECASE)


def parse_pdf(content: bytes) -> tuple[list[RegistryRow], int]:
    """Строки перечня Россетей. Таблица в PDF разрывается по страницам и ячейкам
    непредсказуемо, поэтому строкой считается абзац текста, а номер ЗАК и срок ищутся в нём
    и в двух следующих строках."""

    from app.services.document_extraction import extract_text

    try:
        text = extract_text(".pdf", content) or ""
    except Exception as exc:  # noqa: BLE001 - см. parse_xlsx
        raise RegistryUnavailable(f"PDF не читается: {exc}") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise RegistryUnavailable("В PDF нет текстового слоя (скан?) — сверка возможна только вручную")
    rows: list[RegistryRow] = []
    for index, line in enumerate(lines):
        window = " ".join(lines[index : index + 3])
        number = _ZAK_NUMBER_RE.search(window)
        dates = [_to_date(match.group(0)) for match in _DATE_RE.finditer(window)]
        dates = [item for item in dates if item]
        rows.append(
            RegistryRow(
                record_number=number.group(1) if number else None,
                product_name=line,
                valid_to=max(dates) if dates else None,
                raw=window,
            )
        )
    return rows, len(lines)
