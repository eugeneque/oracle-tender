"""Проверка и приведение значений характеристик перед записью в справочник (замечания
тендерного отдела 13.10.2026, карточка модели).

Что ловится — всё это было в карточках реальных моделей:

* **Код из условного обозначения вместо значения.** Легенда ФГИС объясняет позиции
  обозначения («(Х)1 — количество фаз; Х5 — код класса точности»), но не таблицу кодов, и
  модель записывала сам код: «Количество фаз: 2» у однофазного CE210 (цифра из «CE**2**10»),
  «Индикация: F», «Оптический порт: L», «Класс точности: A1R1». Значение, которое целиком
  совпадает с куском обозначения, отбрасывается (`looks_like_designation_code`).
* **Описание всего семейства в поле одной модели.** «Описание типа» перечисляет все
  исполнения сразу: «в зависимости от корпуса: S10, C10 — 120×180×74 мм; S30, C30 — …».
  Если по обозначению модели понятно, какой вариант её (`C10` в «CE210 C10.746.B SPDS»), —
  берётся он; если нет, значение семейства для «Описания типа» не записывается
  (`resolve_family_value`).
* **Подпись вместо числа.** «Максимальный ток: Iмакс» — у числовых полей значение без
  единой цифры отбрасывается.
* **Формат.** Класс точности — «x/x», как его пишут в ТЗ (A1R1 → 1/1, A0,5SR1 → 0,5S/1);
  фазность — 1 или 3.

Модуль чистый: на вход строки, на выход строки — его же использует кнопка наведения порядка
в уже накопленных данных (`catalog_repair`).
"""

from __future__ import annotations

import re

from app.adapters.manufacturer_catalog import normalise_phases

# Поля, значение которых без единой цифры бессмысленно.
NUMERIC_FIELDS = frozenset(
    {
        "Номинальный ток",
        "Максимальный ток",
        "Стартовый ток",
        "Номинальное напряжение",
        "Диапазон рабочих напряжений",
        "Частота сети",
        "Габаритные размеры",
        "Средняя наработка на отказ",
        "Срок службы",
        "Диапазон рабочих температур",
        "Межповерочный интервал",
        "Масса",
    }
)

# Поля, где кусок обозначения — законное значение: модель, артикул, корпус.
DESIGNATION_FIELDS = frozenset(
    {
        "Модель",
        "Наименование краткое",
        "Наименование полное",
        "Артикул",
        "Бренд",
        "Производитель",
        "Тип корпуса",
        "Номер в Госреестре",
    }
)

_ACCURACY_FIELD = "Класс точности"
_PHASES_FIELD = "Количество фаз"
_CURRENT_FIELDS = frozenset({"Номинальный ток", "Максимальный ток"})

# Латиница и кириллица, совпадающие начертанием: «А1R1» встречается с кириллической А.
_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТХаеорсхУу", "ABEKMHOPCTXaeopcxYy")

_CLASS = r"(\d+(?:[.,]\d+)?\s*S?)"
_ACCURACY_CODE_RE = re.compile(rf"^\s*A\s*{_CLASS}\s*R\s*{_CLASS}\s*$", re.IGNORECASE)
_ACCURACY_ACTIVE_ONLY_RE = re.compile(rf"^\s*A\s*{_CLASS}\s*$", re.IGNORECASE)
_ACCURACY_PAIR_RE = re.compile(rf"^\s*{_CLASS}\s*/\s*{_CLASS}\s*$", re.IGNORECASE)
_ACCURACY_WORDS_RE = re.compile(
    rf"актив\w*[^0-9]{{0,40}}?{_CLASS}.{{0,60}}?реактив\w*[^0-9]{{0,40}}?{_CLASS}", re.IGNORECASE | re.DOTALL
)
_CURRENT_CODE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*[AА]\s*$")

# Признаки того, что значение описывает несколько исполнений сразу.
_FAMILY_MARKERS = re.compile(
    r"в\s+зависимост|для\s+сч[её]тчик|для\s+исполнени|для\s+модификаци|для\s+корпус|для\s+вариант",
    re.IGNORECASE,
)
# Ключ исполнения в перечне: буквы с цифрами — «C10», «W6b», «SP17», «D17».
_KEY_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{1,4}\d{1,3}[A-Za-zА-Яа-я]?")
_SEGMENT_KEYS_FIRST_RE = re.compile(r"^\s*(?P<keys>[^—–:]+?)\s*[—–:]\s*(?P<value>.+)$")
_SEGMENT_KEY_SPACE_RE = re.compile(
    r"^\s*(?P<keys>[A-Za-zА-Яа-яЁё]{1,4}\d{1,3}[A-Za-zА-Яа-я]?)\s+(?P<value>\d.+)$"
)
_SEGMENT_VALUE_FOR_RE = re.compile(
    r"^\s*(?P<value>.+?)\s+для\s+(?:корпус\w*|исполнени\w*|модификаци\w*)\s+(?P<keys>.+)$",
    re.IGNORECASE,
)
_PREFIX_RE = re.compile(r"^\s*в\s+зависимост\w*[^:]*:\s*", re.IGNORECASE)


def _fold(text: str) -> str:
    return (text or "").translate(_HOMOGLYPHS).upper()


def _number(text: str) -> str:
    return re.sub(r"\s+", "", text).replace(".", ",")


def normalise_accuracy_class(value: str) -> str:
    """Класс точности к виду «активная/реактивная», как его пишут в ТЗ."""

    text = value.strip()
    # Коды сравниваются по латинице (замена похожих букв), словесная запись — по исходному
    # тексту: после замены «активной» перестало бы быть кириллицей.
    folded = text.translate(_HOMOGLYPHS)
    for pattern, subject in ((_ACCURACY_CODE_RE, folded), (_ACCURACY_PAIR_RE, folded), (_ACCURACY_WORDS_RE, text)):
        match = pattern.search(subject)
        if match:
            return f"{_number(match.group(1))}/{_number(match.group(2))}"
    match = _ACCURACY_ACTIVE_ONLY_RE.match(folded)
    if match:
        return _number(match.group(1))
    return text


def normalise_value(field_name: str, value: str | None) -> str | None:
    """Приводит значение к виду справочника. `None` — значение негодное и не записывается."""

    if value is None:
        return None
    # Пробелы и переводы строк внутри текста не трогаются: иначе каждое многострочное
    # значение считалось бы «исправленным».
    text = value.strip()
    if not text:
        return None

    if field_name == _ACCURACY_FIELD:
        return normalise_accuracy_class(text)
    if field_name == _PHASES_FIELD:
        # «Однофазные и трёхфазные», «1 или 3» — значение типа целиком, не модели.
        if re.search(r"однофазн", text, re.IGNORECASE) and re.search(r"тр[её]хфазн", text, re.IGNORECASE):
            return None
        if re.search(r"1\D+3|3\D+1", text):
            return None
        phases = normalise_phases(text)
        # Фаз у счётчика электроэнергии бывает одна или три: «2» — это код из обозначения.
        return phases if phases in {"1", "3"} else None
    if field_name in _CURRENT_FIELDS:
        match = _CURRENT_CODE_RE.match(text)
        if match:
            text = f"{match.group(1)} А"
    if field_name in NUMERIC_FIELDS and not re.search(r"\d", text):
        return None
    return text


def is_family_level(value: str | None) -> bool:
    """Значение перечисляет варианты нескольких исполнений, а не одно значение модели."""

    if not value:
        return False
    # «есть (в зависимости от исполнения)» — признак со своей оговоркой, а не перечень: для
    # тендера это ответ «есть, уточнить исполнение», и выбрасывать его незачем.
    if re.match(r"^\s*(есть|да|нет|отсутству\w*)\b", value, re.IGNORECASE):
        return False
    if _FAMILY_MARKERS.search(value):
        return True
    return len(_segments_with_keys(value)) >= 2


def _split_candidates(value: str) -> list[list[str]]:
    text = _PREFIX_RE.sub("", value)
    candidates = [
        # «S10, C10 — 120×180×74 мм; S30, C30 — 165×230×75 мм»
        re.split(r"\s*;\s*", text),
        # «110; 90; 73 мм для корпуса R5; 214; 122; 73 мм для корпуса S7» — точка с запятой
        # и внутри размеров, поэтому делится только после ключа исполнения.
        re.split(r"(?<=[A-Za-zА-Яа-яЁё]\d)\s*[;,]\s*|(?<=[A-Za-zА-Яа-яЁё]\d\d)\s*[;,]\s*", text),
        # «W31: 246;169;57 мм; D31: 131;91;70 мм» — делится только перед ключом с двоеточием.
        re.split(
            r"\s*;\s*(?=(?:для\s+[^;:]{0,30}?)?[A-Za-zА-Яа-яЁё]{1,4}\d{1,3}[A-Za-zа-я]?\s*[:—–])",
            text,
            flags=re.IGNORECASE,
        ),
        # «W1 196×121×60 мм, W2 182×125×55 мм» либо «IP51 для корпуса D17, IP54 для корпуса SP17».
        re.split(
            r",\s*(?=[A-Za-zА-Яа-яЁё]{1,4}\d{1,3}[A-Za-zа-я]?\s+\d|[^,]*\bдля\s+(?:корпус|исполнени|модификаци))",
            text,
            flags=re.IGNORECASE,
        ),
    ]
    return [[part for part in parts if part and part.strip()] for parts in candidates]


def _parse_segment(segment: str) -> tuple[set[str], str] | None:
    match = (
        _SEGMENT_VALUE_FOR_RE.match(segment)
        or _SEGMENT_KEY_SPACE_RE.match(segment)
        or _SEGMENT_KEYS_FIRST_RE.match(segment)
    )
    if not match:
        return None
    keys_text = match.group("keys")
    keys = {_fold(token) for token in _KEY_TOKEN_RE.findall(keys_text)}
    # «для исполнений Z», «для корпуса R» — ключ без цифры, но назван прямо.
    named = re.search(r"(?:исполнени|корпус|модификаци)\w*\s+(.+)$", keys_text, re.IGNORECASE)
    if named:
        keys |= {_fold(token) for token in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", named.group(1))}
    if not keys:
        return None
    return keys, match.group("value").strip(" ,.;")


def _segments(value: str) -> tuple[list[tuple[set[str], str]], list[str]]:
    """Сегменты с ключами исполнений и сегменты без ключа — по лучшему из разбиений."""

    best: tuple[list[tuple[set[str], str]], list[str]] = ([], [])
    for parts in _split_candidates(value):
        keyed, plain = [], []
        for part in parts:
            parsed = _parse_segment(part)
            if parsed:
                keyed.append(parsed)
            else:
                plain.append(part.strip(" ,.;"))
        # Лучшее разбиение — больше сегментов с ключом, при равенстве — меньше без ключа:
        # точка с запятой внутри размеров («110; 90; 73 мм») не должна дробить значение.
        if (len(keyed), -len(plain)) > (len(best[0]), -len(best[1])) or not best[0] and keyed:
            best = (keyed, plain)
    return best


def _segments_with_keys(value: str) -> list[tuple[set[str], str]]:
    return _segments(value)[0]


def designation_tokens(*designations: str | None) -> set[str]:
    """Куски обозначения модели, по которым узнаётся её вариант: «CE210 C10.746.B SPDS» →
    {CE210, C10, 746, B, SPDS}."""

    tokens: set[str] = set()
    for designation in designations:
        for token in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", designation or ""):
            if token:
                tokens.add(_fold(token))
    return tokens


def resolve_family_value(value: str, tokens: set[str]) -> str | None:
    """Вариант значения, относящийся к модели с такими кусками обозначения. `None` — по
    обозначению вариант не выбрать."""

    keyed, plain = _segments(value)
    # Ключ, общий для всех веток («СЕ307» в «для СЕ307 R32: …; для СЕ307 R33: …»), ничего
    # не различает — по нему совпала бы любая ветка.
    common = set.intersection(*(keys for keys, _ in keyed)) if len(keyed) > 1 else set()
    matches = [variant for keys, variant in keyed if (keys - common) & tokens]
    # Две разные подходящие ветки — модель попала под оба условия, выбирать нельзя.
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        return unique[0]
    # «не менее 36 суток…; для исполнений Z: не менее 90 суток» — общее значение и
    # оговорки для отдельных исполнений: если модель ни под одну оговорку не попала,
    # к ней относится общее значение.
    if not unique and keyed and len(plain) == 1 and not _FAMILY_MARKERS.search(plain[0]):
        return plain[0]
    return None


def looks_like_designation_code(value: str, designation: str | None, field_name: str | None = None) -> bool:
    """Значение — это кусок условного обозначения, а не расшифровка: «F», «L1», «A1R1», «1» у
    «RS-485».

    Чистое число считается кодом только у полей, где число не значение: «Количество тарифов:
    4» законно, хотя «4» и встречается в «CE210 C10.746»."""

    # Код класса точности («A1R1») не выбрасывается, а переводится в «1/1» (`normalise_value`).
    if not designation or field_name == _ACCURACY_FIELD or field_name in DESIGNATION_FIELDS:
        return False
    text = value.strip()
    if not text or " " in text or len(text) > 6:
        return False
    if not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё.,/]+", text):
        return False
    if text.casefold() in {"да", "нет", "есть"}:
        return False
    if re.fullmatch(r"[0-9.,/]+", text) and field_name is not None and (
        field_name in NUMERIC_FIELDS or field_name.startswith("Количество")
    ):
        return False
    return _fold(text) in _fold(designation)


# --------------------------------------------------------------------------- сайт → поля

# Строки «вне справочника» с сайта, где перечнем через «;» перечислены возможности прибора.
# Каждая возможность — это поле справочника со значением «есть»; не разложенный перечень
# дублировал поля карточки и в «Вне справочника» выглядел мусором (замечание 13.10.2026, п.8).
LIST_FIELDS = frozenset({"интерфейсы", "дополнительные функции", "функции", "интерфейсы связи"})

_FEATURE_FIELDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"bluetooth", re.IGNORECASE), "Bluetooth"),
    (re.compile(r"оптопорт|оптическ\w+\s+порт|оптическ\w+\s+интерфейс", re.IGNORECASE), "Оптический порт"),
    (re.compile(r"rs-?485", re.IGNORECASE), "RS-485"),
    (re.compile(r"rs-?232", re.IGNORECASE), "RS-232"),
    (re.compile(r"\bplc\b|силов\w+\s+сет", re.IGNORECASE), "PLC"),
    (re.compile(r"\bgsm\b|gprs|\b[34]g\b|lte", re.IGNORECASE), "GSM/GPRS/3G/4G"),
    (re.compile(r"nb-?iot", re.IGNORECASE), "NB-IoT"),
    (re.compile(r"lora", re.IGNORECASE), "LoRaWAN"),
    (re.compile(r"ethernet", re.IGNORECASE), "Ethernet"),
    (re.compile(r"wi-?fi", re.IGNORECASE), "Wi-Fi"),
    (re.compile(r"zigbee", re.IGNORECASE), "ZigBee"),
    (re.compile(r"\brf\b|радио", re.IGNORECASE), "RF-канал"),
    (re.compile(r"реле\s+управлени|\bреле\b", re.IGNORECASE), "Реле управления нагрузкой"),
    (re.compile(r"электронн\w+\s+пломб|вскрыти", re.IGNORECASE), "Контроль вскрытия"),
    (re.compile(r"магнитн\w+\s+пол", re.IGNORECASE), "Контроль магнитного поля"),
    (re.compile(r"подсветк", re.IGNORECASE), "Подсветка дисплея"),
    (re.compile(r"сменн\w+\s+модул", re.IGNORECASE), "Сменный модуль связи"),
    (re.compile(r"датчик\w*\s+наклон", re.IGNORECASE), "Датчик наклона"),
    (re.compile(r"вч[- ]?пол|высокочастотн", re.IGNORECASE), "Датчик ВЧ-поля"),
]

# Строки сайта, которые повторяют поле справочника другими словами.
DUPLICATE_FIELDS: dict[str, str] = {
    "диапазон входных сигналов": "Частота сети",
    "время усреднения профилей нагрузки": "Интервал профиля",
}


def split_feature_list(field_name: str, value: str) -> tuple[list[tuple[str, str]], str | None] | None:
    """Перечень возможностей → ([(поле справочника, «есть»)], нераспознанный остаток).
    `None` — это не перечень либо в нём нет ни одной узнаваемой возможности (тогда строка
    остаётся «вне справочника» целиком). Остаток — пункты без поля в справочнике: они не
    теряются, а остаются «вне справочника»."""

    if field_name.strip().casefold() not in LIST_FIELDS:
        return None
    found: list[tuple[str, str]] = []
    leftover: list[str] = []
    for item in re.split(r"\s*[;\n]\s*", value):
        if not item:
            continue
        matched = False
        for pattern, target in _FEATURE_FIELDS:
            if pattern.search(item):
                matched = True
                if (target, "есть") not in found:
                    found.append((target, "есть"))
        if not matched:
            leftover.append(item)
    if not found:
        return None
    return found, ("; ".join(leftover) or None)
