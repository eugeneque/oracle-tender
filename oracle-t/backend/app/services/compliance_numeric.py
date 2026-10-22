"""Числовые требования, которые матрица соответствия решает кодом, без модели (14.10.2026).

Матрица — самый дорогой вызов модели (~24 тыс. токенов на закупку). Значительная часть
требований — однозначные числа: «Номинальный ток: 5 А», «Класс точности: 0,5S/0,5»,
«Диапазон рабочих температур: от −40 до +70 °C». Сравнить их со значением из каталога —
арифметика, и модель в ней ошибается (см. `apply_duration_check`: «192 месяца меньше 16 лет»).

Принцип — **только однозначное**. Требование разбирается целиком (каждая часть
формулировки узнана, иначе — модели), значение модели каталога — тоже целиком (одно
значение или перечень исполнений; посторонний текст — «не разобрано»). Вердикт на уровне
производителя по всем его моделям того типа, что закупается (CRITERIA.md, п. 3.1 и 3.6):

* «соответствует» — есть модель, у которой значение подходит **без оговорок** (одно
  значение, а не «одно из исполнений»: «только часть исполнений» по методике — «частично»,
  а это решение остаётся модели);
* «не соответствует» — у **каждой** модели нужного типа значение разобрано и не подходит
  ни в одном исполнении. Модель без значения — «нет данных», и тогда решает модель;
* иначе — требование уходит модели, как и раньше.

Какие параметры включены, решено сверкой с вердиктами модели в базе (раздел 59
ARCHITECTURE.md): параметр включается, только если код с моделью не расходится или в
расхождении прав код. Отключённые остаются здесь разобранными, но в `ENABLED` их нет.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# --------------------------------------------------------------------------- общие разборы

_NUM = r"\d+(?:[.,]\d+)?"
_TRANSFORMER_RE = re.compile(r"\bт[тн]\b|трансформатор", re.IGNORECASE)
# Части значения, разделяющие исполнения: «60, 80 или 100 А», «IP51; IP54». Запятая без
# пробела — десятичная («1,5»), поэтому делим только по «, ».
_OPTION_SPLIT = re.compile(r"\s*;\s*|\s+или\s+|,\s+|\s+и\s+", re.IGNORECASE)
_AT_LEAST = re.compile(r"^\s*(?:не\s+менее|не\s+ниже|не\s+хуже|не\s+меньше|≥)\s*", re.IGNORECASE)
_AT_MOST = re.compile(r"^\s*(?:не\s+более|не\s+выше|не\s+больше|≤)\s*", re.IGNORECASE)


def _number(text: str) -> float:
    return float(text.replace(",", ".").replace(" ", ""))


def _options(value: str) -> list[str]:
    return [part.strip(" .") for part in _OPTION_SPLIT.split(value.strip()) if part.strip(" .")]


@dataclass(frozen=True)
class Check:
    """Одно условие требования: параметр, поле каталога, правило сравнения."""

    key: str
    fields: tuple[str, ...]
    # (значение, поле) одной модели → подходит ли каждое исполнение; None — не разобрано.
    judge: Callable[[str, str], list[bool] | None]
    wanted: str


# --------------------------------------------------------------------------- класс точности

_CLASS = r"(\d+(?:[.,]\d+)?)\s*(s)?"
_CLASS_PAIR = re.compile(rf"^{_CLASS}(?:\s*/\s*{_CLASS})?$", re.IGNORECASE)


def _class_key(number: str, s_mark: str | None) -> tuple[float, int]:
    """Меньше — точнее; 0,5S точнее 0,5 (CRITERIA.md, п. 3.1: лучшее значение — подходит)."""

    return (_number(number), 0 if s_mark else 1)


def _parse_class(value: str) -> tuple[tuple[float, int], tuple[float, int] | None] | None:
    match = _CLASS_PAIR.match(value.strip().replace("∕", "/").replace("½", "1/2"))
    if not match:
        return None
    active = _class_key(match.group(1), match.group(2))
    reactive = _class_key(match.group(3), match.group(4)) if match.group(3) else None
    return active, reactive


_CLASS_SEGMENT_SPLIT = re.compile(
    r"[,;]\s*(?=(?:класс\w*\s+точност\w*\s+)?(?:при\s+измерении\s+)?реактивн)", re.IGNORECASE
)


def _accuracy_check(text: str) -> Check | None:
    """«Класс точности активной/реактивной энергии: 0,5S/0,5», «…активной энергии: 1,
    реактивной энергии: 2», «Класс точности: 1 по ГОСТ 31819.21». Классы ТТ и ТН — не
    свойство счётчика, их не трогаем."""

    if _TRANSFORMER_RE.search(text):
        return None
    active: tuple[float, int] | None = None
    reactive: tuple[float, int] | None = None
    for segment in _CLASS_SEGMENT_SPLIT.split(text):
        if ":" not in segment:
            return None
        name, value = segment.split(":", 1)
        name = name.lower()
        value = re.sub(r"\s+по\s+гост.*$", "", value, flags=re.IGNORECASE)
        value = _AT_LEAST.sub("", value).strip(" .")
        parsed = _parse_class(value)
        if parsed is None:
            return None
        first, second = parsed
        if "реактивн" in name and "активн" not in name.replace("реактивн", ""):
            if second is not None:
                return None
            reactive = first
        elif "реактивн" in name or second is not None:
            # «активной/реактивной: 1/2» или «Класс точности: 1/2»: одно число на две
            # энергии — неоднозначно.
            if second is None:
                return None
            active, reactive = first, second
        else:
            active = first
    if active is None and reactive is None:
        return None

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        options = _options(product_value.replace("∕", "/"))
        parsed = [_parse_class(option) for option in options]
        if not parsed or any(item is None for item in parsed):
            return None
        results = []
        for option_active, option_reactive in parsed:  # type: ignore[misc]
            if reactive is not None and option_reactive is None:
                # «1» или «1,0; 2,0» — то ли исполнения, то ли активная и реактивная:
                # реактивную по такому значению не определить.
                return None
            fits = True
            if active is not None:
                fits = fits and option_active <= active
            if reactive is not None:
                fits = fits and option_reactive <= reactive  # type: ignore[operator]
            results.append(fits)
        return results

    return Check("accuracy", ("Класс точности",), judge, text.split(":", 1)[1].strip())


# --------------------------------------------------------------------------- напряжение


def _voltage(value: str) -> tuple[int, float, float | None] | None:
    """«3×230/400 В» → (3, 230, 400); «230 В» → (1, 230, None); иначе None."""

    text = value.lower().replace("×", "x").replace("х", "x").replace("*", "x").replace(",", ".")
    text = re.sub(r"\s+", "", text).rstrip("в").rstrip("b")
    match = re.fullmatch(rf"(?:(\d)x)?(\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?", text)
    if not match:
        return None
    phase = float(match.group(2))
    line = float(match.group(3)) if match.group(3) else None
    phases = int(match.group(1)) if match.group(1) else (3 if line else 1)
    # Прежнее обозначение сетей 220/380 В — то же, что нынешние 230/400 В (ГОСТ 29322-2014),
    # а «380 В» / «400 В» без фазного — линейное напряжение трёхфазной сети. 32616386816
    # (15.10.2026): эталон Милур 307S «не проходил» по «Номинальное напряжение: 380 В» при
    # «3×230/400 В» в каталоге.
    phase = _LEGACY_VOLTAGE.get(phase, phase)
    line = _LEGACY_VOLTAGE.get(line, line) if line is not None else None
    if line is None and phase == 400:
        phases, phase, line = 3, 230.0, 400.0
    return phases, phase, line


_LEGACY_VOLTAGE = {220.0: 230.0, 380.0: 400.0}


def _voltage_check(value: str) -> Check | None:
    wanted = _voltage(value)
    if wanted is None:
        return None

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        parsed = [_voltage(option) for option in _options(product_value)]
        if not parsed or any(item is None for item in parsed):
            return None
        results = []
        for option in parsed:
            if option == wanted:
                results.append(True)
            elif option[0] == wanted[0]:  # type: ignore[index]
                results.append(False)
            else:
                # «230 В» против «3×230/400 В»: фазное напряжение то же, сравнивать нечего.
                return None
        return results

    return Check("voltage", ("Номинальное напряжение",), judge, value.strip())


# --------------------------------------------------------------------------- токи

_CURRENT = re.compile(rf"^({_NUM})\s*(?:\(\s*({_NUM})\s*\)|/\s*({_NUM}))?\s*а?$", re.IGNORECASE)


def _currents(value: str) -> list[tuple[float, float | None]] | None:
    """Исполнения «5 (100) А», «5/60», «60, 80 или 100 А» → [(ток, макс. в скобках)]."""

    result = []
    for option in _options(value):
        match = _CURRENT.match(option.strip())
        if not match:
            return None
        extra = match.group(2) or match.group(3)
        result.append((_number(match.group(1)), _number(extra) if extra else None))
    return result or None


def _current_check(value: str, *, maximum: bool) -> list[Check] | None:
    """«Номинальный ток: 5 А», «5(60) А» (номинальный и максимальный), «не менее 80 А».

    Номинальный — точное совпадение (5 А и 10 А — разные приборы); максимальный «не менее» —
    любое исполнение не меньше; максимальный без оговорки — совпадение, а больший
    максимальный ток без совпадения — не решаем (модели виднее, лучше ли он)."""

    text = value.strip()
    if "±" in text:
        return None
    mode = "exact"
    if _AT_LEAST.match(text):
        mode, text = "at_least", _AT_LEAST.sub("", text)
    elif _AT_MOST.match(text):
        mode, text = "at_most", _AT_MOST.sub("", text)
    match = _CURRENT.match(text.strip())
    if not match:
        return None
    first = _number(match.group(1))
    extra = match.group(2) or match.group(3)
    # В пояснении — с оговоркой «не менее», как в ТЗ: «требуется 100 А» при 80 А читается
    # как ошибка сравнения.
    shown = value.strip()
    if maximum:
        if extra:
            return None
        return [_current_rule("max_current", first, mode, shown)]
    checks = [_current_rule("nominal_current", first, mode, shown)]
    if extra:
        if mode != "exact":
            return None
        checks.append(_current_rule("max_current", _number(extra), "exact", shown))
    return checks


def _current_rule(key: str, wanted: float, mode: str, shown: str) -> Check:
    maximum = key == "max_current"

    def compare(value: float) -> bool | None:
        if mode == "at_least":
            return value >= wanted
        if mode == "at_most":
            return value <= wanted
        if value == wanted:
            return True
        if maximum and value > wanted:
            return None
        return False

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        parsed = _currents(product_value)
        if parsed is None:
            return None
        if maximum and field_name == "Номинальный ток":
            # «5 (100) А» в поле номинального тока — максимальный в скобках.
            if any(extra is None for _, extra in parsed):
                return None
            values = [extra for _, extra in parsed]
        else:
            values = [current for current, _ in parsed]
        results = [compare(value) for value in values]  # type: ignore[arg-type]
        if any(item is None for item in results):
            return None
        return results  # type: ignore[return-value]

    fields = ("Максимальный ток", "Номинальный ток") if maximum else ("Номинальный ток",)
    return Check(key, fields, judge, shown)


# --------------------------------------------------------------------------- частота

_FREQUENCY = re.compile(rf"^\(?\s*({_NUM})\s*(?:±\s*({_NUM}))?\s*\)?\s*(?:гц)?$", re.IGNORECASE)
_FREQUENCY_RANGE = re.compile(rf"^от\s*({_NUM})\s*до\s*({_NUM})\s*(?:гц)?$", re.IGNORECASE)


def _frequency(value: str) -> tuple[float, float] | None:
    text = value.strip(" .")
    match = _FREQUENCY.match(text)
    if match:
        centre = _number(match.group(1))
        spread = _number(match.group(2)) if match.group(2) else 0.0
        return centre - spread, centre + spread
    match = _FREQUENCY_RANGE.match(text)
    if match:
        return _number(match.group(1)), _number(match.group(2))
    return None


def _frequency_check(value: str) -> Check | None:
    wanted = _frequency(value)
    if wanted is None:
        return None

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        # «50±2,5 (60±3)» — два исполнения.
        text = re.sub(r"\)\s*$", "", re.sub(r"\s*\(\s*(?=\d+\s*±)", "; ", product_value))
        parsed = [_frequency(option) for option in _options(text)]
        if not parsed or any(item is None for item in parsed):
            return None
        results = []
        for low, high in parsed:  # type: ignore[misc]
            if low == high and wanted[0] != wanted[1]:
                return None  # отклонение у модели не указано
            results.append(low <= wanted[0] and high >= wanted[1])
        return results

    return Check("frequency", ("Частота сети",), judge, value.strip())


# --------------------------------------------------------------------------- «не менее N»


def _single_number(value: str, unit: str, scale: Callable[[float, str], float] | None = None) -> float | None:
    """Ровно одно число с единицей (или без неё) в значении; иначе None."""

    text = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", value)  # «400 000» → «400000»
    found = re.findall(rf"({_NUM})\s*({unit})?", text, re.IGNORECASE)
    if len(found) != 1:
        return None
    number, written_unit = found[0]
    return scale(_number(number), written_unit.lower()) if scale else _number(number)


def _minimum_check(key: str, fields: tuple[str, ...], value: str, unit: str, scale=None) -> Check | None:
    """Чем больше — тем лучше (наработка, срок службы, МПИ, тарифы): требование «N» или
    «не менее N» выполняется значением не меньше N."""

    text = value.strip()
    if _AT_MOST.match(text):
        return None
    text = _AT_LEAST.sub("", text)
    if re.search(rf"^{_NUM}\s*(?:{unit})?\.?$", re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", text), re.IGNORECASE) is None:
        return None
    wanted = _single_number(text, unit, scale)
    if wanted is None:
        return None

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        cleaned = re.sub(
            r"^\s*(?:средн\w+\s+)?(?:срок\w*\s+службы[^0-9]*?)?(?:не\s+менее|не\s+ниже)?\s*",
            "",
            product_value,
            flags=re.IGNORECASE,
        )
        number = _single_number(cleaned, unit, scale)
        if number is None:
            return None
        return [number >= wanted]

    return Check(key, fields, judge, value.strip())


def _years(number: float, unit: str) -> float:
    return number / 12 if unit.startswith("мес") else number


def _months(number: float, unit: str) -> float | None:
    if unit.startswith("мес"):
        return number
    if unit:
        return number * 12
    # Голое число: «16» в карточке — годы, «192» — месяцы; середина неоднозначна.
    if number <= 30:
        return number * 12
    if number >= 60:
        return number
    return None


def _tariffs_check(value: str) -> Check | None:
    text = re.sub(r"^\s*(?:до|не\s+менее)\s*", "", value.strip(), flags=re.IGNORECASE)
    match = re.fullmatch(r"(\d+)(?:-?х)?\s*(?:тариф\w*)?", text.strip(" ."), re.IGNORECASE)
    if not match:
        return None
    wanted = int(match.group(1))

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        cleaned = re.sub(r"/\s*\d+", "", product_value)  # «4/12» — 4 тарифа в 12 зонах
        if re.search(r"[a-zа-яё]", re.sub(r"не\s+менее|до|от|или|тариф\w*|-?х\b", "", cleaned, flags=re.IGNORECASE), re.IGNORECASE):
            return None
        numbers = [int(item) for item in re.findall(r"\d+", cleaned)]
        if not numbers:
            return None
        return [max(numbers) >= wanted]

    return Check("tariffs", ("Количество тарифов",), judge, f"тарифов {value.strip()}")


# --------------------------------------------------------------------------- температура


def _temperature_range(value: str) -> tuple[float, float] | None:
    text = value.lower()
    text = re.sub(r"минус\s*", "-", text)
    text = re.sub(r"плюс\s*", "+", text)
    text = re.sub(r"[–—−]", "-", text)
    text = re.sub(r"([-+])\s+(?=\d)", r"\1", text)
    numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", text)
    rest = re.sub(r"[-+]?\d+(?:[.,]\d+)?|°|[сc]\b|от|до|\.\.+|…|\s|\(|\)|,", "", text)
    if len(numbers) != 2 or rest:
        return None
    low, high = (_number(item.lstrip("+")) for item in numbers)
    return (low, high) if low < high else None


def _temperature_check(value: str) -> Check | None:
    wanted = _temperature_range(value)
    if wanted is None:
        return None

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        found = _temperature_range(product_value)
        if found is None:
            return None
        return [found[0] <= wanted[0] and found[1] >= wanted[1]]

    return Check("temperature", ("Диапазон рабочих температур",), judge, value.strip())


# --------------------------------------------------------------------------- степень защиты

_IP = re.compile(r"ip\s*(\d)(\d)", re.IGNORECASE)


def _ip_check(value: str) -> Check | None:
    text = _AT_LEAST.sub("", value.strip())
    match = re.fullmatch(r"ip\s*(\d)(\d)", text.strip(" ."), re.IGNORECASE)
    if not match:
        return None
    wanted = (int(match.group(1)), int(match.group(2)))

    def judge(product_value: str, field_name: str = "") -> list[bool] | None:
        codes = [(int(a), int(b)) for a, b in _IP.findall(product_value)]
        if not codes or re.sub(r"ip\s*\d\d|[,;/\s]|или", "", product_value, flags=re.IGNORECASE):
            return None
        return [code[0] >= wanted[0] and code[1] >= wanted[1] for code in codes]

    return Check("ip", ("Степень защиты",), judge, value.strip())


# --------------------------------------------------------------------------- разбор требования

# Название условия → разбор значения. Порядок важен: «максимальный ток» раньше «ток».
_CLAUSES: tuple[tuple[str, re.Pattern[str], Callable[[str], Check | list[Check] | None]], ...] = (
    ("voltage", re.compile(r"^номинальн\w+\s+напряжени\w*(?:\s+переменного\s+тока)?$", re.IGNORECASE), _voltage_check),
    ("max_current", re.compile(r"^максимальн\w+\s+ток\w*$", re.IGNORECASE), lambda v: _current_check(v, maximum=True)),
    ("nominal_current", re.compile(r"^(?:номинальн|базов)\w*\s+(?:\(\w+\)\s+)?ток\w*$", re.IGNORECASE), lambda v: _current_check(v, maximum=False)),
    ("frequency", re.compile(r"^(?:номинальн\w+\s+)?частот\w+(?:\s+сети)?$", re.IGNORECASE), _frequency_check),
    ("mtbf", re.compile(r"^(?:средн\w+\s+)?наработк\w+\s+(?:на|до)\s+отказ\w*$", re.IGNORECASE),
     lambda v: _minimum_check("mtbf", ("Средняя наработка на отказ",), v, r"ч\w*")),
    ("service_life", re.compile(r"^(?:средн\w+\s+)?срок\w*\s+службы(?:\s+счетчика|\s+счётчика)?$", re.IGNORECASE),
     lambda v: _minimum_check("service_life", ("Срок службы",), v, r"лет|года|год|мес\w*", _years)),
    ("mpi", re.compile(r"^межповерочн\w+\s+интервал\w*(?:\s+счетчика|\s+счётчика)?$", re.IGNORECASE),
     lambda v: _minimum_check("mpi", ("Межповерочный интервал",), v, r"лет|года|год|мес\w*", _months)),
    ("tariffs", re.compile(r"^(?:количеств\w+|числ\w+)\s+тариф\w*$|^тарифност\w*$|^многотарифн\w+\s+учет\w*$", re.IGNORECASE), _tariffs_check),
    ("temperature", re.compile(r"^(?:рабоч\w+\s+)?(?:температурн\w+\s+)?диапазон\w*(?:\s+рабоч\w+)?(?:\s+температур\w*)?$|^рабоч\w+\s+температур\w+$", re.IGNORECASE), _temperature_check),
    ("ip", re.compile(r"^степен\w+\s+защиты(?:\s+корпуса)?(?:\s+\(ip\))?$", re.IGNORECASE), _ip_check),
)
_CLAUSE_SPLIT = re.compile(r"[;,]\s*(?=[^:;,]{3,60}:)")

# «Базовый / максимальный ток: не более 5А / не менее 100А» (32616482191, замечание
# 22.10.2026) — два условия в одной строке. Раньше такая строка не разбиралась, а сокращённая
# моделью формулировка «Базовый ток: 5 А» подтверждалась кодом у D17 с максимальным 80 А.
_PAIRED = re.compile(
    r"^(?P<first>[^:/]+?)\s*/\s*(?P<second>[^:/]+?)\s+(?P<noun>\w+)\s*:\s*"
    r"(?P<one>[^/]+?)\s*/\s*(?P<two>[^/]+)$"
)
# Заголовок через тире вместо двоеточия: «Базовый / максимальный ток – не более 5А».
_DASH_HEADING = re.compile(r"^([^:–—]{3,60}?)\s+[–—-]\s+")


def _expand_paired(text: str) -> str:
    if ":" not in text:
        text = _DASH_HEADING.sub(r"\1: ", text, count=1)
    match = _PAIRED.match(text)
    if not match:
        return text
    noun = match.group("noun")
    return (
        f"{match.group('first')} {noun}: {match.group('one')}; "
        f"{match.group('second')} {noun}: {match.group('two')}"
    )


def requirement_checks(text: str) -> list[Check] | None:
    """Условия требования, если оно целиком однозначно; иначе None (решает модель)."""

    text = (text or "").strip().rstrip(".")
    if re.match(r"^класс\w*\s+точност", text, re.IGNORECASE):
        check = _accuracy_check(text) if ":" in text else None
        return [check] if check else None
    text = _expand_paired(text)
    if not text or ":" not in text:
        return None
    if _TRANSFORMER_RE.search(text):
        return None
    checks: list[Check] = []
    for clause in _CLAUSE_SPLIT.split(text):
        if clause.count(":") != 1:
            return None
        name, value = (part.strip() for part in clause.split(":", 1))
        parser = next((parse for _, pattern, parse in _CLAUSES if pattern.match(name)), None)
        if parser is None:
            return None
        parsed = parser(value)
        if not parsed:
            return None
        checks.extend(parsed if isinstance(parsed, list) else [parsed])
    return checks or None


# --------------------------------------------------------------------------- формулировка

# Ссылки на документы и годы — не значения требования: «Класс точности: 1» без «по ГОСТ
# 31819.21-2012» ничего не потерял.
_REFERENCE_RE = re.compile(
    r"(?:гост|ту|сто|iec|мэк|en|постановлени\w*|пп|№)\s*(?:р\s*)?[\d.\-–/ ]*\d", re.IGNORECASE
)
_VALUE_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _value_numbers(text: str) -> set[str]:
    cleaned = _REFERENCE_RE.sub(" ", text or "")
    return {
        number.replace(",", ".")
        for number in _VALUE_NUMBER.findall(cleaned)
        if not re.fullmatch(r"(?:19|20)\d\d", number)
    }


def lost_numbers(text: str | None, normalized: str | None) -> set[str]:
    """Числа формулировки ТЗ, которых нет в сокращённой (`normalized_text`): модель при
    разборе выбросила часть требования — «Базовый / максимальный ток – не более 5А / не
    менее 100А» превратилось в «Базовый ток: 5 А» (32616482191)."""

    if not text or not normalized:
        return set()
    return _value_numbers(text) - _value_numbers(normalized)


def wording_for_code(text: str | None, normalized: str | None) -> str:
    """Что сверять кодом: сокращённую формулировку, если она ничего не потеряла; иначе —
    формулировку ТЗ (если и она не разбирается, требование уходит модели)."""

    if lost_numbers(text, normalized):
        return text or ""
    return normalized or text or ""


# --------------------------------------------------------------------------- вердикт


@dataclass
class CatalogModel:
    """Модель каталога для предрешения: название, значения полей (без руководства —
    это третий шаг сопоставления), типы прибора."""

    name: str
    values: dict[str, str]
    groups: set[str]


# Включённые параметры — все разобранные: сверка 14.10.2026 по 8,7 тыс. ячеек матрицы не
# нашла ни одного «соответствует» кодом, где была бы права модель (раздел 59 ARCHITECTURE.md).
ENABLED = frozenset(
    {"accuracy", "voltage", "nominal_current", "max_current", "frequency", "mtbf",
     "service_life", "mpi", "tariffs", "temperature", "ip"}
)

# Параметры, зависящие от типа прибора (фазность × включение).
KIND_BOUND = frozenset({"accuracy", "voltage", "nominal_current", "max_current"})


# Параметры, по которым код ставит «не соответствует» — только для своего каталога
# (`decisive`, МИРТЕК): ток и число тарифов (замечание 22.10.2026: «требуют максимальный ток
# не менее 100 А, у прибора 80 А», «требуют 8 тарифов, у прибора 4» — и прибор
# предлагался). Каталог МИРТЕК — вся линейка с сайта производителя, «ни одна модель не
# подходит» в нём значит «у нас такого нет»; у конкурентов каталог неполон (см. `prejudge`).
DECISIVE_KEYS = frozenset({"nominal_current", "max_current", "tariffs"})


@dataclass
class NumericVerdict:
    status: str  # "meets" или "not_meets" (только `decisive`)
    explanation: str
    key: str


def _model_fits(checks: list[Check], model: CatalogModel) -> tuple[bool | None, list[str]]:
    """True — подходит без оговорок; False — не подходит ни в одном исполнении; None —
    не разобрано, нет значения или подходит лишь часть исполнений."""

    shown: list[str] = []
    verdicts: list[bool | None] = []
    for check in checks:
        field_name = next((name for name in check.fields if model.values.get(name)), None)
        if field_name is None:
            verdicts.append(None)
            continue
        value = model.values[field_name]
        shown.append(f"{field_name.lower()} {value}")
        results = check.judge(value, field_name)
        if results is None:
            verdicts.append(None)
        elif all(results) and len(results) == 1:
            verdicts.append(True)
        elif not any(results):
            verdicts.append(False)
        else:
            verdicts.append(None)  # часть исполнений — «частично», решает модель
    if any(item is False for item in verdicts):
        return False, shown
    if all(item is True for item in verdicts):
        return True, shown
    return None, shown


def prejudge(
    text: str,
    models: list[CatalogModel],
    wanted_groups: set[str] | None,
    enabled: frozenset[str],
    *,
    decisive: bool = False,
) -> NumericVerdict | None:
    """Вердикт кодом по требованию `text` для производителя с моделями `models`.

    `wanted_groups` — группы типов приборов закупки (фазность × включение): сравниваются
    только модели этих групп (CRITERIA.md, п. 3.6). Не определены — все модели.

    `decisive` — каталог производителя полон (свой, МИРТЕК): по `DECISIVE_KEYS` код ставит и
    «не соответствует», если у каждой модели нужного типа значение разобрано и не подходит."""

    checks = requirement_checks(text)
    if not checks or any(check.key not in enabled for check in checks):
        return None
    if any(check.key in KIND_BOUND for check in checks) and len(wanted_groups or ()) != 1:
        # Ток, напряжение и класс точности зависят от типа прибора: в многопозиционной
        # закупке (однофазные и трёхфазные) или с неопределённым типом неясно, к какой
        # позиции относится требование — «Базовый ток: 5 А» у однофазного не ответ для
        # трёхфазной позиции. Это решает модель.
        return None
    candidates = [m for m in models if not wanted_groups or (m.groups & wanted_groups)]
    wanted = "; ".join(check.wanted for check in checks)
    failed: list[str] = []
    for model in candidates:
        fits, shown = _model_fits(checks, model)
        if fits:
            return NumericVerdict(
                "meets",
                f"{model.name}: {', '.join(shown)} (требуется {wanted}) [сверено кодом]"[:300],
                checks[0].key,
            )
        if fits is False:
            failed.append(f"{model.name}: {', '.join(shown)}")
    if (
        decisive
        and candidates
        and len(failed) == len(candidates)
        and all(check.key in DECISIVE_KEYS for check in checks)
    ):
        return NumericVerdict(
            "not_meets",
            f"Требуется {wanted}; ни одна модель не подходит — {'; '.join(failed)} [сверено кодом]"[:300],
            checks[0].key,
        )
    # «Не соответствует» код не ставит: сверка 14.10.2026 показала, что каталог моделей
    # неполон (у Милура в нём только 5(10) А, а приборы на 100 А модель нашла в описании
    # типа), и «ни одна модель каталога не подходит» ≠ «производитель такого не делает»
    # (CRITERIA.md, п. 3: отсутствие факта — «нет данных»). Такие требования решает модель.
    return None
