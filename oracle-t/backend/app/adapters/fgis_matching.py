"""Дисамбигуация кандидатов реестра ФГИС — какой из найденных типов СИ действительно
относится к искомому прибору (раздел 5.3 ТЗ, п.1 алгоритма заполнения каталога).

**Зачем отдельный модуль.** Поиск в реестре полнотекстовый (см. докстринг
`app/adapters/fgis.py`), и по названию/бренду он регулярно возвращает приборы совсем другого
вида измерений. Реально воспроизведённый случай — «Пульсар»: под этой торговой маркой в
Госреестре лежат и счётчики электрической энергии, и извещатели пожарные, и расходомеры
воды, причём от разных юрлиц. Автосохранение первого попавшегося кандидата подкладывало в
справочник «Описание типа» пожарного извещателя вместо электросчётчика — то есть модуль
сопоставления начинал сверять требования тендера с характеристиками чужого прибора.

**Что делает фильтр.** Справочник продукции проекта ограничен счётчиками электрической
энергии (раздел 2.2.1, 2.2.2 ТТ — и список конкурентов, и перечень характеристик целиком про
электросчётчики), поэтому у кандидата проверяется **вид измерений**, а не только совпадение
названия: наименование типа в реестре («Счетчики электрической энергии однофазные
многофункциональные») прямо называет класс прибора.

**Что делает вызывающий код.** Функция ничего не решает за него: она возвращает либо
единственного уверенного кандидата, либо вердикт «требует ручной проверки» со списком всех
рассмотренных вариантов и причиной. Сохранять или нет — решает сервис каталога, и по п.1.2
задания при неоднозначности он обязан пометить запись, а не выбирать самостоятельно.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol


class DeviceKind(str, enum.Enum):
    """Вид средства измерений. Пока нужен один — справочник продукции ограничен
    электросчётчиками, — но перечисление, а не константа-строка: когда в проект добавят
    приборы учёта воды/тепла (в ТЗ их нет), новый вид добавится строкой, без переработки
    вызывающего кода."""

    ELECTRICITY_METER = "electricity_meter"


class SiTypeCandidate(Protocol):
    """Минимум, который фильтру нужен от кандидата. Протокол, а не импорт `SiSearchResult`:
    так модуль тестируется без сетевого слоя адаптера и не зависит от него по импортам."""

    si_code: str
    type_name: str | None
    notation: str | None
    manufacturer_name: str | None
    matched_by: str


# Наименование типа, по которому прибор опознаётся как счётчик электрической энергии.
# «Счётчик» отдельно от «электрическ…» намеренно: в реестре встречаются и «Счетчики
# электрической энергии», и «Счетчики электроэнергии», и «Счетчики активной электрической
# энергии», и написание через «е» вместо «ё» — общая часть у всех одна.
_ELECTRICITY_METER_PATTERNS = (
    re.compile(r"счет\w*\s+(?:\w+\s+){0,3}?электр", re.IGNORECASE),
    re.compile(r"счет\w*\s+электроэнерг", re.IGNORECASE),
    re.compile(r"счет\w*\s+(?:\w+\s+){0,3}?активной\s+и\s+реактивной", re.IGNORECASE),
)

# Виды приборов, которые полнотекстовый поиск подмешивает в выдачу по тем же торговым маркам.
# Список не «все прочие приборы мира», а именно те классы, что реально ловились по названиям
# из раздела 4.3 ТЗ: пожарная сигнализация («Пульсар»), приборы учёта других ресурсов
# (у тех же производителей под тем же брендом), измерительные трансформаторы.
_FOREIGN_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("извещатель пожарный", re.compile(r"извещател\w*\s+пожарн", re.IGNORECASE)),
    ("прибор пожарной сигнализации", re.compile(r"пожарн\w*\s+сигнализац", re.IGNORECASE)),
    ("счётчик воды", re.compile(r"счет\w*\s+(?:\w+\s+){0,2}?вод[ыу]", re.IGNORECASE)),
    ("счётчик газа", re.compile(r"счет\w*\s+(?:\w+\s+){0,2}?газа", re.IGNORECASE)),
    ("теплосчётчик", re.compile(r"теплосчет\w*|счет\w*\s+(?:\w+\s+){0,2}?тепл", re.IGNORECASE)),
    ("расходомер", re.compile(r"расходомер", re.IGNORECASE)),
    ("тепловычислитель", re.compile(r"тепловычислител", re.IGNORECASE)),
    ("трансформатор", re.compile(r"трансформатор", re.IGNORECASE)),
    ("манометр", re.compile(r"манометр", re.IGNORECASE)),
    ("датчик давления", re.compile(r"датчик\w*\s+давлен", re.IGNORECASE)),
)

_KIND_LABELS = {DeviceKind.ELECTRICITY_METER: "счётчик электрической энергии"}


def classify_si_type(type_name: str | None, notation: str | None = None) -> DeviceKind | None:
    """Какой вид СИ описан наименованием типа из реестра.

    `None` — «не опознан»: это не то же самое, что «чужой прибор». Наименования в реестре
    формулируются свободно, и глухой отказ по неопознанному наименованию терял бы законные
    записи. Такой кандидат уходит человеку на проверку, а не отбрасывается молча (см.
    `pick_candidate`).
    """

    haystack = " ".join(part for part in (type_name, notation) if part)
    if not haystack.strip():
        return None

    normalised = haystack.replace("ё", "е")
    if any(pattern.search(normalised) for pattern in _ELECTRICITY_METER_PATTERNS):
        return DeviceKind.ELECTRICITY_METER
    return None


def foreign_kind_label(type_name: str | None, notation: str | None = None) -> str | None:
    """Название чужого вида прибора, если наименование типа прямо на него указывает.

    Нужно ровно для сообщения человеку и для журнала: «отклонён кандидат 12345-16 —
    извещатель пожарный» гораздо полезнее, чем «отклонён кандидат 12345-16»."""

    haystack = " ".join(part for part in (type_name, notation) if part)
    if not haystack.strip():
        return None
    normalised = haystack.replace("ё", "е")
    for label, pattern in _FOREIGN_KIND_PATTERNS:
        if pattern.search(normalised):
            return label
    return None


@dataclass
class RejectedCandidate:
    si_code: str
    type_name: str | None
    manufacturer_name: str | None
    reason: str

    def describe(self) -> str:
        return f"{self.si_code} «{self.type_name or '—'}» ({self.manufacturer_name or 'изготовитель не указан'}) — {self.reason}"


@dataclass
class CandidateDecision:
    """Решение по выдаче реестра.

    Три взаимоисключающих исхода, и вызывающий код обязан различать все три:

    * `accepted is not None и not needs_review` — единственный кандидат нужного вида,
      можно сохранять автоматически;
    * `needs_review` — кандидатов несколько, либо вид измерений не совпал/не опознан:
      запись сохраняется со статусом «требует ручной проверки», в журнал уходит WARNING
      со списком всех кандидатов (п.1.2 задания);
    * ни того, ни другого — выдача пуста, искать нечего.
    """

    accepted: SiTypeCandidate | None = None
    needs_review: bool = False
    reason: str = ""
    considered: list[SiTypeCandidate] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)

    @property
    def has_result(self) -> bool:
        return self.accepted is not None

    def describe_candidates(self) -> str:
        """Однострочное описание всех рассмотренных вариантов — для журнала и для подсказки
        человеку в интерфейсе."""

        parts = [
            f"{c.si_code} «{c.type_name or '—'}» ({c.manufacturer_name or 'изготовитель не указан'})"
            for c in self.considered
        ]
        parts.extend(item.describe() for item in self.rejected)
        return "; ".join(parts) if parts else "кандидатов нет"


def pick_candidate(
    candidates: Iterable[SiTypeCandidate],
    *,
    expected_kind: DeviceKind = DeviceKind.ELECTRICITY_METER,
    model_name: str | None = None,
) -> CandidateDecision:
    """Выбирает единственного кандидата или требует проверки человеком (п.1.2 задания).

    Порядок отбора, от самого надёжного признака к самому слабому:

    1. **Вид измерений.** Кандидаты, чьё наименование типа прямо называет другой класс
       прибора (пожарный извещатель, счётчик воды…), отбрасываются с указанием причины —
       это и есть защита от коллизии наименований вроде «Пульсара».
    2. **Обозначение типа.** Если известна искомая модель и ровно одно обозначение типа с
       ней совпадает, берётся оно: реестр по одному бренду отдаёт десятки типов, и без
       этого шага любой производитель с широкой линейкой всегда уходил бы «на проверку».
    3. **Способ сопоставления с производителем.** Совпадение по юридическому названию
       (`matched_by == "legal"`) надёжнее совпадения только по торговой марке: марка не
       обязана принадлежать нужному юрлицу — ровно на этом и ловится «Пульсар».

    Автоматически принимается только кандидат, оставшийся единственным после этих шагов.
    Всё остальное — `needs_review`: ошибочно сохранённая карточка чужого прибора дороже,
    чем строка, которую человек подтвердит руками.
    """

    considered = list(candidates)
    if not considered:
        return CandidateDecision(reason="Реестр ФГИС не вернул ни одной записи")

    survivors: list[SiTypeCandidate] = []
    rejected: list[RejectedCandidate] = []
    unknown_kind: list[SiTypeCandidate] = []

    for candidate in considered:
        type_name = getattr(candidate, "type_name", None)
        notation = getattr(candidate, "notation", None)
        kind = classify_si_type(type_name, notation)
        if kind is expected_kind:
            survivors.append(candidate)
            continue

        foreign = foreign_kind_label(type_name, notation)
        if foreign is not None:
            rejected.append(
                RejectedCandidate(
                    si_code=getattr(candidate, "si_code", "?"),
                    type_name=type_name,
                    manufacturer_name=getattr(candidate, "manufacturer_name", None),
                    reason=f"это {foreign}, а не {_KIND_LABELS[expected_kind]}",
                )
            )
            continue

        # Вид не опознан — кандидат не отбрасывается, но и автоматически не принимается:
        # наименования в реестре формулируются свободно, и глухой отказ терял бы законные
        # записи с непривычной формулировкой.
        unknown_kind.append(candidate)

    if not survivors and not unknown_kind:
        return CandidateDecision(
            needs_review=True,
            reason=(
                f"Ни один из {len(considered)} кандидатов реестра не является "
                f"{_KIND_LABELS[expected_kind]}"
            ),
            considered=[],
            rejected=rejected,
        )

    if not survivors:
        return CandidateDecision(
            needs_review=True,
            reason=(
                f"Вид измерений не удалось определить по наименованию типа ни у одного из "
                f"{len(unknown_kind)} кандидатов — требуется проверка человеком"
            ),
            considered=unknown_kind,
            rejected=rejected,
        )

    if len(survivors) == 1:
        return CandidateDecision(accepted=survivors[0], considered=survivors, rejected=rejected)

    by_notation = _filter_by_model(survivors, model_name)
    if len(by_notation) == 1:
        return CandidateDecision(accepted=by_notation[0], considered=survivors, rejected=rejected)
    if by_notation:
        survivors = by_notation

    by_legal = [c for c in survivors if getattr(c, "matched_by", "legal") == "legal"]
    if len(by_legal) == 1:
        return CandidateDecision(accepted=by_legal[0], considered=survivors, rejected=rejected)

    return CandidateDecision(
        needs_review=True,
        reason=(
            f"Реестр вернул {len(survivors)} подходящих типов СИ — однозначно выбрать нельзя, "
            "нужна проверка человеком"
        ),
        considered=survivors,
        rejected=rejected,
    )


def _filter_by_model(
    candidates: list[SiTypeCandidate], model_name: str | None
) -> list[SiTypeCandidate]:
    """Кандидаты, чьё обозначение типа совпадает с искомой моделью.

    Сравнение по «скелету» строки (только буквы и цифры, латиница приведена к кириллице):
    в реестре обозначение приходит в разных кавычках и с разными дефисами
    («МИРТЕК-12-РУ», "МИРТЕК 12 РУ"), а в каталоге модель записана как на сайте
    производителя. Латиница важна отдельно: «МИРТЕК-12-РУ» на сайте и «MИPTEK-12-PY» в
    реестре — визуально одно и то же, но разные кодовые точки."""

    if not model_name:
        return []
    target = _skeleton(model_name)
    if not target:
        return []

    matched = []
    for candidate in candidates:
        notation = _skeleton(getattr(candidate, "notation", None) or "")
        if not notation:
            continue
        # Вхождение проверяется в обе стороны, и это не перестраховка: обозначение типа в
        # реестре обычно короче названия модели на сайте («МИРТЕК-12-РУ» покрывает целое
        # семейство «МИРТЕК-12-РУ-D17», «…-SP17»), но встречается и обратное. Порог длины
        # отсекает вырожденные совпадения по обрывку из пары символов.
        if notation == target or (
            len(min(notation, target, key=len)) >= 4
            and (notation in target or target in notation)
        ):
            matched.append(candidate)
    return matched


# Латинские омоглифы кириллических букв — только те, что визуально неотличимы. Таблица
# применяется к уже приведённой к нижнему регистру строке.
_LATIN_TO_CYRILLIC = str.maketrans("abcehkmoptxy", "авсенкмортху")


def _skeleton(value: str) -> str:
    lowered = value.lower().replace("ё", "е").translate(_LATIN_TO_CYRILLIC)
    return re.sub(r"[^0-9a-zа-я]+", "", lowered)
