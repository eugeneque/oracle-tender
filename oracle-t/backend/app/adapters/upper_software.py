"""Списки поддерживаемого оборудования ПО верхнего уровня (замечание тестировщика 16.09.2026).

**Зачем.** В ТЗ закупок регулярно стоит требование «интеграция в ПО верхнего уровня»
— часто с названием конкретного продукта: «поддержка в ПК „Энергосфера“», «интеграция с
ПО „Пирамида 2.0“», «совместимость с яЭнергетик». Ответить на него по каталогу
производителя нельзя: интегрирован прибор или нет, знает не производитель, а разработчик
ПО, и публикует это на своём сайте списком поддерживаемых устройств. Семь таких списков
названы тестировщиком; этот адаптер их читает, а `app/services/upper_software_service.py`
сопоставляет с моделями справочника и подаёт факты в матрицу соответствия.

**Семь площадок — семь разметок.** Общего шаблона нет, и парсер у каждой свой — как в
`manufacturer_catalog.py`, селекторы проверены на живых страницах (разведка 16.09.2026),
а не угаданы. Что показала разведка и что определило код:

* **Пирамида (sicon.ru)** — три таблицы `table.devlist-table` (ПО «Пирамида 2.0» /
  «Пирамида-Сети», контроллер SM-160, СИКОН/ИВК). Колонки «Производитель» и «Тип
  оборудования» объединены через `rowspan` на десяток строк — без раскрытия объединений
  производитель оказался бы только у первой модели семейства. Есть **номер в Госреестре**
  — самый надёжный ключ сопоставления со справочником.
* **Энфорс (nforceit.ru)** — абзацы «14. производства ООО "МИРТЕК":» и следом
  `ul > li` с моделями. Номер и слово «производства» — служебные, производитель — остаток
  абзаца.
* **Энергосфера (prosoftsystems.ru)** — таблица `table.data-thin`: устройства через
  запятую в одной ячейке, номера ГРСИ во второй (иногда объединённой `rowspan` на несколько
  строк), флаги поддержки в третьей. **Производителя в таблице нет** — он выводится по
  номеру ГРСИ либо по бренду в названии. Секции («УСПД», «Счётчики электроэнергии
  СПОДЭС», «Счётчики электроэнергии», «Расходомеры…») — строки с одной ячейкой.
* **яЭнергетик (yaenergetik.ru)** — вкладка `section#tab-meters`, в строке ссылка с
  названием и `span.device-factory-title` с производителем; функции и каналы связи —
  в атрибуте `data-filter` (`f:power_profile n:rs485`).
* **АльфаЦЕНТР (alphacnt.ru)** — новость на ASP.NET: `span#ContentPlaceHolder1_lblReply`
  с незакрытыми `<li>`, модели жирным `span`, производитель — остаток текста пункта.
  Раздел «Все счетчики, поддерживающие протокол СПОДЭС … В том числе:» — поддержка
  по протоколу, а не по конкретной модели; такие записи помечаются `is_generic`.
* **Некта (nekta.tech)** — список отдаётся AJAX-запросом `admin-ajax.php?action=filter_prod`
  порциями по 24, а первый запрос без cookie `beget=begetok` возвращает страницу-заглушку
  с перезагрузкой. Фильтр по типу «Счётчик электричества» (`type[]=86`) — остальные 400
  устройств (датчики, модемы) справочнику не нужны.
* **ЛЭРС учёт (lersuchet.ru)** — `h2#электросчетчики` и следом `ul.wp-block-list`, в
  каждом `li` модели одного производителя через запятую, производитель не назван.

Что общего вынесено сюда: раскрытие `rowspan`/`colspan`, разбиение перечислений моделей,
выделение номеров ГРСИ, снятие скобочных пометок («(СПОДЭС-4)»). Сетевой слой —
`http_utils.fetch_with_retry`, как у остальных адаптеров.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import httpx
from bs4 import BeautifulSoup, Tag
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify

# Чужие публичные сайты: один запрос за раз, без спешки — как в обходе каталогов.
REQUEST_TIMEOUT = 40.0

# Номер в Госреестре СИ: «61891-15», «17049-19». Ловим только полный вид с годом —
# просто число рядом с моделью («ST2000-12») номером не является.
_SI_CODE_RE = re.compile(r"\b(\d{4,6}-\d{2})\b")

# Скобочные пометки при модели: «(СПОДЭС-4)», «(протокол СПОДЭС)», «(по каналу NBIOT)».
# К обозначению не относятся, но в ключ сопоставления попали бы.
_PARENS_RE = re.compile(r"\s*\([^)]*\)")

# Хвост «включая получение инициативных сообщений…» у Энфорса, «- многотарифные» у
# АльфаЦЕНТР: описание, а не часть обозначения.
_TRAILING_NOTE_RE = re.compile(
    r"\s+(?:[-–—]\s*)?(?:включая|с версией|версии|по протокол|по каналу|только|многотарифн|"
    r"многофункц|однофазн|трёхфазн|трехфазн).*$",
    re.IGNORECASE,
)

# Кусок перечисления, который целиком описание, а не обозначение: «Вектор-101, однофазные».
_DESCRIPTIVE_PART_RE = re.compile(
    r"^(?:однофазн|трёхфазн|трехфазн|многотарифн|многофункц|модульн|сплит|split)\w*$",
    re.IGNORECASE,
)

# Разделители перечислений моделей в одной ячейке: «МИРТЕК-1 , МИРТЕК-12-РУ, МИРТЕК-3».
_LIST_SPLIT_RE = re.compile(r"\s*[,;]\s*|\s+и\s+")


@dataclass
class SupportedDevice:
    """Одна строка списка поддерживаемого оборудования."""

    section: str
    device_raw: str
    device_names: list[str]
    manufacturer_raw: str | None = None
    si_codes: list[str] = field(default_factory=list)
    device_type: str | None = None
    # Поддержка «любого счётчика по протоколу СПОДЭС» — не подтверждение по конкретной
    # модели, и в матрице соответствия такая запись весит меньше.
    is_generic: bool = False
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PlatformProfile:
    key: str
    name: str
    vendor: str
    url: str
    # HTML страницы → записи. Для Некты страница собирается из нескольких AJAX-ответов,
    # поэтому у профиля есть и собственный загрузчик.
    parser: Callable[[str], list[SupportedDevice]]
    # Как ПО называют в закупочной документации — шаблоны без учёта регистра.
    aliases: tuple[str, ...]
    fetcher: Callable[[httpx.Client, "PlatformProfile"], list[SupportedDevice]] | None = None
    cookies: dict[str, str] | None = None


# --------------------------------------------------------------------------- общие хелперы


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def split_device_names(raw: str) -> list[str]:
    """Перечисление моделей → отдельные обозначения без пометок.

    «МИРТЕК-32-РУ (протокол СПОДЭС), МИРТЕК-135-РУ (СПОДЭС)» → ["МИРТЕК-32-РУ",
    "МИРТЕК-135-РУ"]. Хвост-описание («включая получение инициативных сообщений по
    каналам TCP/UDP, HDLC») снимается до разбиения — иначе «HDLC» стал бы моделью.
    Сокращённая запись Энергосферы «МИЛУР-104, -105, -107S» раскрывается по предыдущему
    обозначению: «-105» → «МИЛУР-105». Пустые и односимвольные куски отбрасываются."""

    cleaned = _TRAILING_NOTE_RE.sub("", _PARENS_RE.sub("", raw))
    names: list[str] = []
    prefix = ""
    for part in _LIST_SPLIT_RE.split(cleaned):
        part = part.strip()
        shorthand = part.startswith(("-", "."))
        part = _TRAILING_NOTE_RE.sub("", part).strip(" .:;–-")
        if not part:
            continue
        if shorthand and prefix:
            part = f"{prefix}-{part}"
        elif not shorthand:
            head, separator, _ = part.rpartition("-")
            prefix = head.strip() if separator else ""
        if len(part) >= 2 and part not in names and not _DESCRIPTIVE_PART_RE.match(part):
            names.append(part)
    return names


def extract_si_codes(raw: str) -> list[str]:
    codes: list[str] = []
    for code in _SI_CODE_RE.findall(raw or ""):
        if code not in codes:
            codes.append(code)
    return codes


def expand_table(table: Tag) -> list[list[str]]:
    """Таблица → прямоугольная сетка текстов с раскрытыми `rowspan`/`colspan`.

    У Пирамиды производитель стоит один раз на 12 строк семейства, у Энергосферы номер
    ГРСИ — один на несколько строк исполнений; без раскрытия объединений эти значения
    достались бы только первой строке."""

    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}
    for row_index, row in enumerate(table.find_all("tr")):
        cells = row.find_all(["td", "th"], recursive=False)
        if not cells:
            cells = row.find_all(["td", "th"])
        values: list[str] = []
        column = 0
        cell_iter = iter(cells)
        current = next(cell_iter, None)
        while current is not None or (row_index, column) in pending:
            if (row_index, column) in pending:
                values.append(pending.pop((row_index, column)))
                column += 1
                continue
            text = _text(current)
            rowspan = _int_attr(current, "rowspan")
            colspan = _int_attr(current, "colspan")
            for span_col in range(colspan):
                values.append(text)
                for span_row in range(1, rowspan):
                    pending[(row_index + span_row, column + span_col)] = text
            column += colspan
            current = next(cell_iter, None)
        grid.append(values)
    return grid


def _int_attr(cell: Tag, name: str) -> int:
    try:
        return max(1, int(cell.get(name) or 1))
    except (TypeError, ValueError):
        return 1


def _is_generic(text: str) -> bool:
    lowered = text.lower()
    return ("все счетчики" in lowered or "все счётчики" in lowered or "любые счетчики" in lowered
            or "любые счётчики" in lowered or lowered.startswith("сподэс-"))


# ------------------------------------------------------------------------ Пирамида (sicon)

_SICON_SECTIONS = {
    "devlist-2": "ПО «Пирамида 2.0» / «Пирамида-Сети»",
    "devlist-3": "Контроллер SM-160",
    "devlist-1": "СИКОН, ИВК «Пирамида», «Пирамида 2000»",
}


def parse_sicon(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    devices: list[SupportedDevice] = []
    for table in soup.find_all("table", class_="devlist-table"):
        section = _SICON_SECTIONS.get(table.get("id", ""), table.get("id") or "Пирамида")
        grid = expand_table(table)
        header_rows = [row for row in grid if row and not row[0].isdigit()]
        data_rows = [row for row in grid if row and row[0].isdigit()]
        if not header_rows or not data_rows:
            continue
        headers = _merge_headers(header_rows)
        for row in data_rows:
            if len(row) < 5:
                continue
            manufacturer, device_type, device, si_raw = row[1], row[2], row[3], row[4]
            if not device:
                continue
            flags = {
                headers[index]: value.strip() != ""
                for index, value in enumerate(row)
                if 5 <= index < len(headers) and headers[index] and "примечан" not in headers[index].lower()
            }
            note = ""
            for index, value in enumerate(row):
                if index < len(headers) and "примечан" in headers[index].lower():
                    note = value
            devices.append(
                SupportedDevice(
                    section=section,
                    device_raw=device,
                    device_names=split_device_names(device),
                    manufacturer_raw=manufacturer or None,
                    si_codes=extract_si_codes(si_raw),
                    device_type=device_type or None,
                    details={"support": flags, **({"note": note} if note else {})},
                )
            )
    return devices


# Шапка у Пирамиды свёрстана с переносами внутри слов («Через промежу<br>точное
# оборуд<br>ование»), и после склейки текста в словах остаются пробелы. Имена колонок
# нужны человеку в карточке, поэтому приводятся к читаемому виду по ключевому слову.
_SICON_HEADER_ALIASES = (
    ("прямой", "прямой канал связи"),
    ("оборуд", "через промежуточное оборудование"),
    ("промежу", "через промежуточное ПО"),
)


def _normalize_sicon_header(value: str) -> str:
    lowered = value.lower()
    for needle, replacement in _SICON_HEADER_ALIASES:
        if needle in lowered:
            return replacement
    return value


def _merge_headers(header_rows: list[list[str]]) -> list[str]:
    """Двухуровневая шапка → одно имя на колонку: «Режим взаимодействия: прямой канал связи»."""

    width = max(len(row) for row in header_rows)
    merged: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row in header_rows:
            if column < len(row) and row[column] and (not parts or row[column] != parts[-1]):
                parts.append(_normalize_sicon_header(row[column]))
        merged.append(": ".join(parts))
    return merged


# ------------------------------------------------------------------------- Энфорс (nforceit)

_NFORCE_HEADING_RE = re.compile(r"^\s*\d+\.\s*(?:производства\s*|компания\s*)?(.+?):?\s*$", re.IGNORECASE)


def parse_nforceit(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    devices: list[SupportedDevice] = []
    content = soup.find("div", class_="support-accordion-item-content") or soup
    manufacturer: str | None = None
    for node in content.find_all(["p", "ul"]):
        if node.name == "p":
            match = _NFORCE_HEADING_RE.match(_text(node))
            if match:
                manufacturer = match.group(1).strip(" :")
            continue
        if manufacturer is None:
            continue
        for item in node.find_all("li"):
            raw = _text(item)
            if not raw:
                continue
            devices.append(
                SupportedDevice(
                    section="ПО «Энфорс»",
                    device_raw=raw,
                    device_names=split_device_names(raw),
                    manufacturer_raw=manufacturer,
                    si_codes=extract_si_codes(raw),
                    device_type="УСПД" if raw.upper().startswith("УСПД") else None,
                )
            )
    return devices


# --------------------------------------------------------------- Энергосфера (prosoftsystems)

# Легенда флагов из таблицы: «+» — опрос, Т — тарифы, П — параметрирование, У — управление
# нагрузкой, К — контроль качества. Расшифровка нужна человеку в карточке, модели — нет.
_ENERGOSPHERE_FLAGS = {
    "+": "опрос данных",
    "Т": "синхронизация времени",
    "T": "синхронизация времени",
    "П": "параметрирование тарифных расписаний",
    "У": "управление нагрузкой",
    "К": "контроль качества электроэнергии",
}


def parse_prosoft(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="data-thin")
    if table is None:
        return []
    devices: list[SupportedDevice] = []
    section = "ПК «Энергосфера»"
    grid = expand_table(table)
    for row in grid:
        if not row:
            continue
        non_empty = [value for value in row if value]
        if len(set(non_empty)) == 1 and (len(row) == 1 or all(value == row[0] for value in row)):
            # Строка-секция («УСПД», «Счётчики электроэнергии»): одна ячейка на всю ширину.
            section = f"ПК «Энергосфера»: {row[0]}"
            continue
        if row[0].lower().startswith("тип модуля"):
            continue
        if len(row) < 2:
            continue
        # Две ячейки — номер ГРСИ объединён с предыдущей строкой, но объединение не дошло
        # (усечённая таблица); устройство и флаги при этом на месте.
        device_raw, si_raw, flags_raw = (row[0], row[1], row[2]) if len(row) >= 3 else (row[0], "", row[1])
        if not device_raw:
            continue
        flags = [
            _ENERGOSPHERE_FLAGS[token]
            for token in re.findall(r"[+ТTПУК]", flags_raw)
            if token in _ENERGOSPHERE_FLAGS
        ]
        devices.append(
            SupportedDevice(
                section=section,
                device_raw=device_raw,
                device_names=split_device_names(device_raw),
                si_codes=extract_si_codes(si_raw),
                is_generic=_is_generic(device_raw),
                details={"functions": list(dict.fromkeys(flags))},
            )
        )
    return devices


# ------------------------------------------------------------------- яЭнергетик (yaenergetik)

_YAENERGETIK_FEATURES = {
    "readings": "сбор показаний",
    "readings_daily": "показания на начало суток",
    "readings_monthly": "показания на начало месяца",
    "power_profile": "профиль мощности",
    "parameters": "параметры электроэнергии",
    "power_quality": "контроль качества электроэнергии",
    "relay_control": "управление нагрузкой",
    "time_control": "коррекция времени",
}


def parse_yaenergetik(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    devices: list[SupportedDevice] = []
    for panel_key, section in (("meters", "Электросчётчики"), ("uspds", "УСПД")):
        panel = soup.find("section", attrs={"data-device-panel": panel_key})
        if panel is None:
            continue
        for row in panel.find_all("tr", attrs={"data-device-row": True}):
            link = row.find("a")
            if link is None:
                continue
            name = _text(link)
            factory = row.find("span", class_="device-factory-title")
            filters = (row.get("data-filter") or "").split()
            features = [
                _YAENERGETIK_FEATURES.get(token[2:], token[2:])
                for token in filters
                if token.startswith("f:")
            ]
            channels = [token[2:].upper() for token in filters if token.startswith("n:")]
            devices.append(
                SupportedDevice(
                    section=f"АСКУЭ яЭнергетик: {section}",
                    device_raw=name,
                    device_names=split_device_names(name),
                    manufacturer_raw=_text(factory) or None,
                    device_type="УСПД" if panel_key == "uspds" else "Счётчик электрической энергии",
                    details={
                        "functions": features,
                        "channels": channels,
                        "url": link.get("href"),
                    },
                )
            )
    return devices


# --------------------------------------------------------------------- АльфаЦЕНТР (alphacnt)


def parse_alphacenter(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("span", id="ContentPlaceHolder1_lblReply") or soup
    devices: list[SupportedDevice] = []
    section = "ПО «АльфаЦЕНТР»"
    for node in container.descendants:
        if not isinstance(node, Tag):
            continue
        # Заголовки разделов («Счетчики электрической энергии», «УСПД») — зелёный span
        # вне списка; всё остальное жирное — модели внутри пунктов.
        if node.name == "span" and "green" in (node.get("style") or "") and node.find_parent("li") is None:
            heading = _text(node)
            if heading:
                section = f"ПО «АльфаЦЕНТР»: {heading}"
            continue
        if node.name != "li":
            continue
        own_text = _li_own_text(node)
        if not own_text:
            continue
        if _is_generic(own_text):
            # «Все счетчики, поддерживающие протокол СПОДЭС … В том числе:» — поддержка
            # по протоколу, а не по модели; вложенный список — примеры таких приборов.
            devices.append(
                SupportedDevice(
                    section=section,
                    device_raw=own_text,
                    device_names=[],
                    is_generic=True,
                    details={"protocol": "СПОДЭС" if "СПОДЭС" in own_text.upper() else "DLMS"},
                )
            )
            continue
        bold = node.find("span", style=re.compile("bold"))
        if bold is None:
            continue
        device_raw = _text(bold)
        rest = own_text.replace(device_raw, "", 1).strip(" ,;:")
        via_protocol = node.find_parent("ul", attrs={"type": "disc"}) is not None
        devices.append(
            SupportedDevice(
                section=section,
                device_raw=device_raw,
                device_names=split_device_names(device_raw),
                manufacturer_raw=rest or None,
                si_codes=extract_si_codes(own_text),
                details=(
                    {"via_protocol": "СПОДЭС", "note": "в перечне приборов, работающих по протоколу СПОДЭС"}
                    if via_protocol
                    else {}
                ),
            )
        )
    return devices


def _li_own_text(item: Tag) -> str:
    """Текст пункта без вложенных списков: у АльфаЦЕНТР `<li>` не закрыты, и html.parser
    складывает следующий пункт внутрь предыдущего."""

    parts: list[str] = []
    for child in item.children:
        if isinstance(child, Tag) and child.name in ("li", "ul", "ol"):
            break
        parts.append(child.get_text(" ", strip=True) if isinstance(child, Tag) else str(child))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


# ---------------------------------------------------------------------------- Некта (nekta)

NEKTA_AJAX_URL = "https://nekta.tech/wp-admin/admin-ajax.php"
NEKTA_PAGE_SIZE = 24
NEKTA_ELECTRICITY_TYPE = "86"
NEKTA_MAX_PAGES = 40


def parse_nekta(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    devices: list[SupportedDevice] = []
    for row in soup.find_all("a", class_="tr"):
        cells = [_text(cell) for cell in row.find_all("span", class_="td")]
        if len(cells) < 5:
            continue
        _, name, manufacturer, device_type, protocols = cells[:5]
        if not name:
            continue
        devices.append(
            SupportedDevice(
                section="ПК «Некта»",
                device_raw=name,
                device_names=split_device_names(name),
                manufacturer_raw=manufacturer or None,
                device_type=device_type or None,
                details={
                    "channels": [p.strip() for p in protocols.split(",") if p.strip()],
                    "url": row.get("href"),
                },
            )
        )
    return devices


def fetch_nekta(client: httpx.Client, profile: PlatformProfile) -> list[SupportedDevice]:
    """Список Некты отдаётся только AJAX-запросом порциями по 24 — страница целиком
    показывает первые 24 устройства всех типов."""

    devices: list[SupportedDevice] = []
    for page in range(NEKTA_MAX_PAGES):
        response = fetch_with_retry(
            client,
            "POST",
            NEKTA_AJAX_URL,
            data={
                "action": "filter_prod",
                "offset": str(page * NEKTA_PAGE_SIZE),
                "type[]": NEKTA_ELECTRICITY_TYPE,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        batch = parse_nekta(response.text)
        devices.extend(batch)
        if len(batch) < NEKTA_PAGE_SIZE:
            break
    return devices


# ----------------------------------------------------------------------- ЛЭРС учёт (lersuchet)


def parse_lers(html: str) -> list[SupportedDevice]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h2", id="электросчетчики")
    if heading is None:
        heading = next(
            (h for h in soup.find_all("h2") if "электросчет" in _text(h).lower()), None
        )
    if heading is None:
        return []
    devices: list[SupportedDevice] = []
    node = heading
    while True:
        node = node.find_next_sibling()
        if node is None or node.name == "h2":
            break
        if node.name != "ul":
            continue
        for item in node.find_all("li", recursive=False):
            raw = _text(item)
            if not raw:
                continue
            devices.append(
                SupportedDevice(
                    section="ЛЭРС УЧЁТ: Электросчётчики",
                    device_raw=raw,
                    device_names=split_device_names(raw),
                    device_type="Счётчик электрической энергии",
                    is_generic=_is_generic(raw),
                )
            )
    return devices


# ------------------------------------------------------------------------------- профили

PLATFORMS: dict[str, PlatformProfile] = {
    "piramida": PlatformProfile(
        key="piramida",
        name="ПО «Пирамида»",
        vendor="АО ГК «Системы и Технологии»",
        url="https://sicon.ru/prod/podderzhivaemoe-oborudovanie/",
        parser=parse_sicon,
        aliases=(r"пирамид", r"pyramid", r"sicon", r"сикон"),
    ),
    "enforce": PlatformProfile(
        key="enforce",
        name="ПО «Энфорс»",
        vendor="ООО «Энфорс»",
        url="https://nforceit.ru/support/",
        parser=parse_nforceit,
        aliases=(r"энфорс", r"nforce", r"enforce"),
    ),
    "energosphere": PlatformProfile(
        key="energosphere",
        name="ПК «Энергосфера»",
        vendor="ООО «Прософт-Системы»",
        url="https://prosoftsystems.ru/catalog/show/spisok-podderzhivaemyh-ustrojstv",
        parser=parse_prosoft,
        aliases=(r"энергосфер", r"energosphere", r"прософт"),
    ),
    "yaenergetik": PlatformProfile(
        key="yaenergetik",
        name="АСКУЭ «яЭнергетик»",
        vendor="ООО «яЭнергетик»",
        url="https://yaenergetik.ru/devices/",
        parser=parse_yaenergetik,
        aliases=(r"я\s*энергетик", r"yaenergetik"),
    ),
    "alphacenter": PlatformProfile(
        key="alphacenter",
        name="ПО «АльфаЦЕНТР»",
        vendor="ООО «АльфаЦЕНТР»",
        url="https://www.alphacnt.ru/cgi-bin/ViewNews.aspx?newsid=9&newstype=1",
        parser=parse_alphacenter,
        aliases=(r"альфа\s*-?\s*центр", r"alpha\s*-?\s*cent", r"альфацентр"),
    ),
    "nekta": PlatformProfile(
        key="nekta",
        name="ПК «Некта»",
        vendor="ООО «Некта»",
        url="https://nekta.tech/catalog/",
        parser=parse_nekta,
        aliases=(r"\bнекта\b", r"\bnekta\b"),
        fetcher=fetch_nekta,
        cookies={"beget": "begetok"},
    ),
    "lers": PlatformProfile(
        key="lers",
        name="ПО «ЛЭРС УЧЁТ»",
        vendor="ООО «ЛЭРС»",
        url="https://lersuchet.ru/devices/",
        parser=parse_lers,
        aliases=(r"лэрс", r"\blers\b"),
    ),
}


def build_client(profile: PlatformProfile) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        cookies=profile.cookies or {},
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
        verify=resolve_verify(profile.url),
    )


def fetch_platform(
    profile: PlatformProfile, *, client: httpx.Client | None = None
) -> list[SupportedDevice]:
    """Скачивает и разбирает список одной площадки. Сетевые ошибки не глотаются — сервис
    решает, как их изолировать от остальных площадок."""

    own_client = client is None
    client = client or build_client(profile)
    try:
        if profile.fetcher is not None:
            devices = profile.fetcher(client, profile)
        else:
            response = fetch_with_retry(client, "GET", profile.url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            devices = profile.parser(response.text)
        logger.info(f"ПО верхнего уровня {profile.key}: прочитано записей {len(devices)}")
        return devices
    finally:
        if own_client:
            client.close()
