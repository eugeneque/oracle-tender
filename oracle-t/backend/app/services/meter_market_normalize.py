"""Нормализация написаний для справочника рынка ПУ (раздел 79 ARCHITECTURE.md, раздел 6 ТЗ
`ARSHIN_ANALYTICS.md`).

Поверители пишут одну и ту же модификацию по-разному: «НАРТИС-И300» латиницей вперемешку с
кириллицей, «0» вместо «О», двойные пробелы и табуляции. Визуально это одна строка, а для
`GROUP BY` — десятки разных. Ключ (`modification_key`) сводит такие написания автоматически;
всё, что ключом не свелось, уходит человеку в очередь «Разметить».
"""

from __future__ import annotations

import re

# Кириллица, совпадающая по начертанию с латиницей, — к латинице (после casefold, поэтому
# только строчные). «в» после casefold — это бывшая «В», похожая на латинскую «B».
_HOMOGLYPHS = str.maketrans("авекмнорстхуё", "abekmhopctxye")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def modification_key(raw: str | None) -> str:
    """Ключ сведения написаний модификации: регистр, омоглифы, 0/О, пробелы и пунктуация.

    Пунктуация убирается вся: «НАРТИС-100.121RIL» и «НАРТИС-100 121RIL» — одно исполнение,
    а различие между ними — только в том, как поверитель набрал строку."""

    if not raw:
        return ""
    text = raw.casefold().translate(_HOMOGLYPHS).replace("0", "o")
    return _NON_ALNUM_RE.sub("", text)


def clean_text(value: object) -> str | None:
    """Строка из ячейки без табуляций и повторных пробелов; пустое и «н/д»-подобное — `None`."""

    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


# Интерфейсы: в файле аналитика за 3 кв. 2026 — 68 написаний, из них половина — одно и то же
# с другим регистром или хвостовым пробелом («GSM» / «GSM », «LoRaWan» / «LoRaWAN»).
_INTERFACE_TOKENS: dict[str, str] = {
    "rs": "RS",
    "gsm": "GSM",
    "rf": "RF",
    "plc": "PLC",
    "lorawan": "LoRaWAN",
    "nbiot": "NB-IoT",
    "nb-iot": "NB-IoT",
    "nb-fi": "NB-Fi",
    "nbfi": "NB-Fi",
    "zigbee": "ZigBee",
    "bluetooth": "Bluetooth",
    "ethernet": "Ethernet",
    "wi-fi": "Wi-Fi",
    "wifi": "Wi-Fi",
    "4g": "4G",
    "mod": "mod",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def normalize_interface(value: object) -> str | None:
    """Интерфейс в едином написании: составные значения сохраняются как у аналитика
    («GSM, PLC, ZigBee, Bluetooth», «RF и RS»), меняется только регистр известных
    названий и пробелы. «н/д» остаётся «н/д» — это отдельный столбец её отчёта."""

    text = clean_text(value)
    if text is None:
        return None

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        return _INTERFACE_TOKENS.get(token.casefold(), token)

    text = _TOKEN_RE.sub(repl, text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*\+\s*", " + ", text)
    return text.strip(" ,")


def normalize_design(value: object) -> str:
    """Исполнение к коду: «Шкафное»/«шкафное», «Din- рейка» и «н/д» (раздел 6.2 ТЗ)."""

    text = (clean_text(value) or "").casefold().replace(" ", "")
    if text.startswith("шкаф"):
        return "cabinet"
    if text.startswith("сплит") or text.startswith("split"):
        return "split"
    if "din" in text or "рейк" in text:
        return "din"
    return "nd"


def normalize_phase(value: object) -> str | None:
    text = (clean_text(value) or "").casefold()
    if text.startswith("1"):
        return "1ф"
    if text.startswith("3"):
        return "3ф"
    return None


_LEGAL_FORM_RE = re.compile(r"^\s*(ООО|АО|ЗАО|ПАО|ОАО|ИП|ФГУП)(?=[\s\"«]|$)\s*", re.IGNORECASE)
_QUOTES = "\"«»„“”'"


def manufacturer_display_name(raw: str) -> str:
    """Имя производителя для отчёта: без организационно-правовой формы и с ровными кавычками.

    `ООО"Завод НАРТИС"` → «Завод НАРТИС», `ООО "НПК "Инкотекс"` → «НПК «Инкотекс»»,
    `ООО Завод "Промприбор"` → «Завод «Промприбор»». Имя потом правится в справочнике —
    функция только задаёт разумное начальное значение."""

    text = clean_text(raw) or ""
    text = _LEGAL_FORM_RE.sub("", text, count=1).strip()
    if text and text[0] in _QUOTES:
        text = text[1:]
        if text and text[-1] in _QUOTES:
            text = text[:-1]
    text = text.strip()
    match = re.match(r"^([^\"«»]+?)\s*[\"«](.+?)[\"»]*$", text)
    if match:
        return f"{match.group(1).strip()} «{match.group(2).strip()}»"
    return text.strip(_QUOTES + " ")


def manufacturer_key(raw: str | None) -> str:
    """Ключ синонима производителя: `ООО"МИРТЕК"` и `ООО "МИРТЕК"` — одно и то же."""

    if not raw:
        return ""
    text = _LEGAL_FORM_RE.sub("", clean_text(raw) or "", count=1)
    return _NON_ALNUM_RE.sub("", text.casefold().replace("ё", "е"))


# Названия модификаций в справочнике аналитика — не сырые написания из поверок, а шаблоны, по
# которым её Excel искал поверки `ILIKE '%…%'` (сверка 29.10.2026: из 1 013 строк файла буквально
# в ФГИС нашлись 384). Устроены они так:
# - «НАРТИС-И100-W112-2» покрывает «НАРТИС-И100-W112-2-A1R1-230-5-80A-…» — поиск подстроки;
# - пробел и многоточие — пропуск, причём порядок слов не важен: «CE207 R7 GS04 oag» находит
#   «CE207 R7.749.2.OAG.QUVLF GS04», «307-...-PZZ-3-» — «307-…-PZZ-3-»;
# - пустая модификация («(без модификации)») — все поверки типа, если не подошло ничего точнее;
# - косая с пробелом — «или»: «МИР С-05 G2Z1B e / G5Z1B» (вторая альтернатива заменяет похожее
#   слово первой — G2Z1B, а не «e»); без пробела — часть кода («G3/R4»), кроме «G/R».
_WILDCARD_RE = re.compile(r"\.{3,}|…|\*|%|\s+")


def _similar(a: str, b: str) -> int:
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common * 10 - abs(len(a) - len(b))


def pattern_parts(name: str | None) -> list[set[str]]:
    """Шаблон модификации — по порядку «слоты», в каждом — допустимые ключи нормализации."""

    text = name or ""
    alternatives = [part for part in re.split(r"\s+/\s*|\s*/\s+", text) if part.strip()]
    if not alternatives:
        return []
    slots: list[set[str]] = []
    for token in _WILDCARD_RE.split(alternatives[0]):
        pieces = [modification_key(t) for t in token.split("/")]
        # «G3/R4» — буквальная часть кода (так и пишут поверители: «…BCS G3/R4»), «G/R» —
        # «G или R»: косая без пробелов означает «или» только между одиночными знаками.
        if len(pieces) > 1 and all(len(p) <= 1 for p in pieces):
            keys = {p for p in pieces if p}
        else:
            keys = {modification_key(token)} - {""}
        if keys:
            slots.append(keys)
    for extra in alternatives[1:]:
        for token in _WILDCARD_RE.split(extra):
            key = modification_key(token)
            if not key or not slots:
                continue
            target = max(range(len(slots)), key=lambda i: max(_similar(key, k) for k in slots[i]))
            slots[target].add(key)
    return slots


def contains_all(key: str, slots: list[set[str]]) -> bool:
    """Каждый слот шаблона есть в ключе написания (в любом месте и порядке)."""

    return all(any(option in key for option in options) for options in slots)


EMPTY_MODIFICATION = "(без модификации)"


class ModificationMatcher:
    """Привязка сырого написания к модификации того же типа по шаблонам названий.

    Побеждает самый специфичный шаблон (больше букв и цифр в слотах): написание «NP73 rs g»
    подходит и под «NP73 rs», и под «NP73 rs g» — это второе. Два разных шаблона одинаковой
    специфичности — неоднозначность, такое написание уходит человеку в «Разметить»."""

    def __init__(self, modifications: list[tuple[int, int, str]], notations: dict[int, str | None] | None = None) -> None:
        self._by_type: dict[int, list[tuple[int, list[set[str]], int]]] = {}
        # Буквенное начало обозначения типа («i-prom.» у «i-prom.3»): поверители его часто
        # опускают — «3-1/2-M-RG-Y-Y» вместо «i-prom.3-1/2-M-RG-Y-Y», тип и так задан номером ГРСИ.
        self._stems: dict[int, set[str]] = {}
        for type_id, notation in (notations or {}).items():
            stems = set()
            for part in re.split(r"[,;]", notation or ""):
                match = re.match(r"\D+", modification_key(part))
                if match and len(match.group(0)) >= 3:
                    stems.add(match.group(0))
            if stems:
                self._stems[type_id] = stems
        for mod_id, type_id, name in modifications:
            self.add(mod_id, type_id, name)

    def add(self, mod_id: int, type_id: int, name: str) -> None:
        if name == EMPTY_MODIFICATION:
            self._by_type.setdefault(type_id, []).append((mod_id, [], 0))
            return
        slots = pattern_parts(name)
        for options in slots:
            for stem in self._stems.get(type_id, ()):
                options |= {option[len(stem):] for option in list(options) if option.startswith(stem) and len(option) > len(stem)}
        if slots:
            weight = sum(min(len(k) for k in options) for options in slots)
            self._by_type.setdefault(type_id, []).append((mod_id, slots, weight))

    def match(self, type_id: int, key: str) -> int | None:
        best: list[tuple[int, int]] = []
        for mod_id, slots, weight in self._by_type.get(type_id, ()):
            if contains_all(key, slots):
                best.append((weight, mod_id))
        if not best:
            return None
        best.sort(reverse=True)
        top_weight = best[0][0]
        top = {mod_id for weight, mod_id in best if weight == top_weight}
        return best[0][1] if len(top) == 1 else None
