"""Форм-фактор модели прибора: фазность (1ф/3ф) и способы установки (сплит, DIN-рейка,
щит/шкаф). Нужен справочнику, чтобы раскладывать модели производителя по столбцам
исполнений, а не сплошным списком (предложение тестировщика 18.09.2026).

Оба признака выводятся, а не хранятся: они целиком определяются характеристиками из
Приложения C («Количество фаз», «Тип монтажа», «Тип корпуса») и наименованием модели.
Отдельная колонка в `products` дублировала бы характеристики и расходилась бы с ними при
каждой повторной экстракции.

Порядок источников — как в разделе 5.3 ТЗ: сначала характеристика (в ней могло быть ручное
значение), потом наименование. Наименование как источник ненадёжно ровно настолько,
насколько ненадёжны обозначения серий у производителей: «Меркурий 20x — однофазные,
23x — трёхфазные» верно сегодня, но это соглашение завода, а не стандарт. Поэтому серии
перечислены явно и по производителям, а не выведены «первой цифрой номера» для всех.

Модель может подходить под несколько способов установки («DIN-рейка ; 3 винта» —
универсальный корпус) — тогда она попадает в каждый столбец. Но если код корпуса есть в
самом обозначении (МИРТЕК-12-РУ-SP17), модель стоит ровно в одном столбце: перечень из
нескольких способов у такой модели — значит, перечень всей серии (14.10.2026). Не определено — пустой
список, а не «щит по умолчанию»: в интерфейсе такие модели показываются отдельно, чтобы
было видно, у кого характеристики ещё не заполнены."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MOUNT_SPLIT = "split"
MOUNT_DIN = "din"
MOUNT_PANEL = "panel"

FIELD_PHASES = "Количество фаз"
FIELD_MOUNTING = "Тип монтажа"
FIELD_BODY = "Тип корпуса"


@dataclass
class FormFactor:
    phases: int | None = None
    mountings: list[str] = field(default_factory=list)


# Серии, у которых фазность зашита в обозначении. Регулярные выражения — по обозначению
# модели (`model_code`) либо наименованию, без учёта регистра.
_PHASES_BY_SERIES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\bМИРТЕК-1\d*\b"), 1),
    (re.compile(r"\bМИРТЕК-3\d*\b"), 3),
    (re.compile(r"\bМеркурий\s*20\d"), 1),
    (re.compile(r"\bМеркурий\s*23\d"), 3),
    (re.compile(r"\bНАРТИС-(?:И100|Р1)\b"), 1),
    (re.compile(r"\bНАРТИС-(?:И300|Р3)\b"), 3),
    (re.compile(r"\bМилур\s*1\d\d"), 1),
    (re.compile(r"\bМилур\s*3\d\d"), 3),
    (re.compile(r"\bНЕВА\s*(?:МТ\s*)?1\d\d\b"), 1),
    (re.compile(r"\bНЕВА\s*(?:МТ\s*)?3\d\d\b"), 3),
    (re.compile(r"\bНЕВА\s*(?:СТ\s*)?2\d\d\b"), 1),
    (re.compile(r"\bНЕВА\s*(?:СТ\s*)?4\d\d\b"), 3),
    (re.compile(r"\bНЕВА\s*СП1\b"), 1),
    (re.compile(r"\bНЕВА\s*СП3\b"), 3),
    (re.compile(r"\bРиМ\s*189\b"), 1),
    (re.compile(r"\bРиМ\s*[24]89\b"), 3),
    (re.compile(r"\bРТМ-01\b"), 1),
    (re.compile(r"\bРТМ-03\b"), 3),
    (re.compile(r"\bФОБОС\s*1\b"), 1),
    (re.compile(r"\bФОБОС\s*3\b"), 3),
    (re.compile(r"\b[CС][EЕ](?:10\d|20\d|901)\b"), 1),
    (re.compile(r"\b[CС][EЕ]30\d\b"), 3),
    (re.compile(r"\bЦЭ6807(?!\d)"), 1),
    (re.compile(r"\bЦЭ68(?:03|04|05|11|22|23|27|50)(?!\d)"), 3),
]

_PHASES_WORDS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"однофазн", re.IGNORECASE), 1),
    (re.compile(r"тр[её]хфазн", re.IGNORECASE), 3),
]


def _phases_from_value(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"[13]", value)
    return int(m.group(0)) if m else None


def _phases_from_name(*names: str | None) -> int | None:
    for name in names:
        if not name:
            continue
        for pattern, phases in _PHASES_WORDS:
            if pattern.search(name):
                return phases
        for pattern, phases in _PHASES_BY_SERIES:
            if pattern.search(name):
                return phases
    return None


# Ключевые слова для способов установки — по значениям, которые реально встречаются
# в характеристиках с сайтов производителей и в «Описании типа».
_MOUNT_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"сплит|split|на опор|опору\b|наружн|\bSP\d", re.IGNORECASE), MOUNT_SPLIT),
    (re.compile(r"din|дин[- ]?рейк|рейк|тн[- ]?35|th[- ]?35|компактн|\bD\d\b", re.IGNORECASE), MOUNT_DIN),
    (re.compile(r"винт|щит|шкаф|панел|моноблок|стен[уеа]\b|\bW\d", re.IGNORECASE), MOUNT_PANEL),
]
# «Универсальная установка/корпус» у производителей означает DIN-рейка + винты.
_UNIVERSAL = re.compile(r"универсальн", re.IGNORECASE)


def _mountings_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for pattern, mount in _MOUNT_KEYWORDS:
        if pattern.search(text) and mount not in found:
            found.append(mount)
    if _UNIVERSAL.search(text):
        for mount in (MOUNT_DIN, MOUNT_PANEL):
            if mount not in found:
                found.append(mount)
    return found


# Обозначения корпусов в кодах моделей: у Энергомеры «-Р» — рейка, «-Ш» — щит
# (ЦЭ6807Б-Р, ЦЭ6807Б-Ш1; CE102-R5, CE102-S6), у Ротек «С1 (Сплит)» / «D1».
_MOUNT_BY_CODE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bЦЭ68\d\d\S*[- ]Р\d*\b|\b[CС][EЕ]\d{3}\S*-R\d+\b"), MOUNT_DIN),
    (re.compile(r"\bЦЭ68\d\d\S*[- ]Ш\d*\b|\b[CС][EЕ]\d{3}\S*-S\d+\b"), MOUNT_PANEL),
    (re.compile(r"\bРТМ-0\d\s+D\d\b"), MOUNT_DIN),
    (re.compile(r"\bРТМ-0\d\s+С\d\b"), MOUNT_SPLIT),
]


def _mountings_from_name(*names: str | None) -> list[str]:
    found: list[str] = []
    for name in names:
        if not name:
            continue
        if re.search(r"сплит|split", name, re.IGNORECASE) and MOUNT_SPLIT not in found:
            found.append(MOUNT_SPLIT)
        for pattern, mount in _MOUNT_BY_CODE:
            if pattern.search(name) and mount not in found:
                found.append(mount)
    return found


# Код корпуса в самом обозначении модели: МИРТЕК-12-РУ-**SP17**, НАРТИС-И100-**W115**,
# МИРТЕК-32-РУ-**D31**. Буква — способ установки (SP — сплит, D — DIN-рейка, W — щиток), цифры —
# исполнение корпуса. Ищется отдельным сегментом обозначения (между дефисами/пробелами), чтобы
# не цеплять буквы внутри кодов интерфейсов и функций.
_BODY_CODE_RE = re.compile(r"(?:^|[\s\-‑])(SP|D|W)(\d{1,3}[a-z]?)(?=$|[\s\-‑,.;)])")
_BODY_PREFIX_MOUNT = {"SP": MOUNT_SPLIT, "D": MOUNT_DIN, "W": MOUNT_PANEL}
# Как способ установки записывается в характеристику, когда его пришлось выбрать из перечня
# серии, — теми же словами, что у МИРТЕК на сайте.
MOUNT_LABELS = {MOUNT_SPLIT: "Сплит", MOUNT_DIN: "DIN-рейка", MOUNT_PANEL: "Щиток"}


def body_code(*designations: str | None) -> str | None:
    """Код корпуса из обозначения модели («SP17») или `None`, если его там нет."""

    for designation in designations:
        match = _BODY_CODE_RE.search(designation or "")
        if match:
            return match.group(1) + match.group(2)
    return None


def mounting_from_designation(*designations: str | None) -> str | None:
    """Способ установки, названный обозначением модели: код корпуса МИРТЕК/НАРТИС
    («-SP17») либо обозначения Энергомеры и Ротек («CE102-R5», «ЦЭ6807Б-Ш1», «РТМ-03 D1»).
    `None` — обозначение способа не называет или называет несколько."""

    code = body_code(*designations)
    if code:
        prefix = "SP" if code.startswith("SP") else code[0]
        return _BODY_PREFIX_MOUNT[prefix]
    # Только коды корпуса, а не слово «сплит» в наименовании: слово пишут и в названиях
    # серий, а ручная характеристика должна оставаться важнее его.
    found = {
        mount
        for designation in designations
        if designation
        for pattern, mount in _MOUNT_BY_CODE
        if pattern.search(designation)
    }
    return found.pop() if len(found) == 1 else None


def model_specific_value(
    field_name: str,
    value: str | None,
    *,
    model_code: str | None,
    model_name: str | None,
    family_source: bool,
) -> str | None:
    """Значение «Тип монтажа» / «Тип корпуса» для одной модели, а не для всей серии.

    Причина (замечание 14.10.2026, «МИРТЕК-12-РУ-SP17 стоит и в Сплите, и на DIN-рейке»):
    «Описание типа» ФГИС — один документ на весь номер Госреестра (61891-15 описывает
    корпуса W1…W9, D1…D17, SP1…SP17 сразу), и модель переносила в каждую модель серии общий
    перечень «на щиток, на DIN-рейку, на опору ЛЭП» / «W1, W2, …, SP17». По такому значению
    справочник ставил модель во все три столбца, а сопоставление с ТЗ считало, что сплит
    подходит под «крепление на DIN-рейку».

    Перечень сужается по коду корпуса в обозначении модели. Если код в обозначении есть, а
    его способа в перечне нет, значение из «Описания типа» не записывается (`None`): оно
    противоречит самой модели. Без кода перечень остаётся — у записи серии («МИРТЕК-101»,
    «СЕ 101») он верен, а у модели с универсальным корпусом («DIN-рейка ; 3 винта») — тоже.
    """

    if field_name not in (FIELD_MOUNTING, FIELD_BODY) or not value:
        return value
    mounts = _mountings_from_text(value)
    if len(mounts) < 2:
        return value
    code = body_code(model_code, model_name)
    own = mounting_from_designation(model_code, model_name)
    if own is None:
        return value
    if own in mounts:
        return code if field_name == FIELD_BODY and code else MOUNT_LABELS[own]
    return None if family_source else value


def classify(
    *,
    model_name: str | None,
    model_code: str | None,
    registry_modification: str | None,
    characteristics: dict[str, str | None],
) -> FormFactor:
    """`characteristics` — значения по имени поля Приложения C для одной модели."""

    phases = _phases_from_value(characteristics.get(FIELD_PHASES))
    if phases is None:
        phases = _phases_from_name(model_code, model_name, registry_modification)

    # Код корпуса в обозначении («-SP17», «-D1», «-W9») — самый надёжный признак: он
    # называет исполнение именно этой модели, а характеристики могли прийти из документа
    # на всю серию (см. `model_specific_value`). Поэтому он важнее характеристик.
    own = mounting_from_designation(model_code, model_name)
    if own:
        return FormFactor(phases=phases, mountings=[own])

    mountings = _mountings_from_text(characteristics.get(FIELD_MOUNTING))
    if not mountings:
        mountings = _mountings_from_text(characteristics.get(FIELD_BODY))
    if not mountings:
        mountings = _mountings_from_name(model_code, model_name)
    # Условное обозначение из реестра содержит код корпуса (НАРТИС-И100-**W115**-…,
    # …-**SP31**-…) — берётся последним: в нём же встречаются буквы интерфейсов и функций,
    # которые могут совпасть с шаблоном.
    if not mountings and registry_modification:
        mountings = _mountings_from_text(registry_modification)
    return FormFactor(phases=phases, mountings=mountings)
