"""Тип прибора учёта — «расширенный нейминг» из файла «Параметры для ПУ» (25.09.2026).

Тендерный отдел делит счётчики на одиннадцать типов: фазность × способ включения ×
способ крепления, плюс высоковольтный прибор учёта (ВПУ). Тип нужен в трёх местах:

* фильтр списка закупок — «покажи тендеры на трёхфазные полукосвенного включения»;
* отбор моделей производителя в контекст сопоставления — в карточку должны попасть
  приборы того же типа, что закупает заказчик, а не первые по алфавиту;
* подпись модели в каталоге.

Тип выводится кодом по правилам из комментариев файла, а не спрашивается у модели:

* **напряжение** — 230 В: прямое или полукосвенное включение; 57,7 В: трансформаторное
  (косвенное); 6 кВ, 10 кВ: высоковольтный прибор учёта;
* **максимальный ток** — 60, 80, 100 А: прямое включение; 10 А (и 7,5 А): полукосвенное
  либо трансформаторное — какое именно, решает напряжение;
* **крепление** — DIN-рейка; на опору ЛЭП — сплит-исполнение; на три винта или в шкаф —
  шкафное; непосредственно на ЛЭП — ВПУ;
* **выносной индикатор** (индикаторное устройство) бывает только у сплит-исполнения и ВПУ.

Номинальный ток 5 А по файлу встречается у всех типов, поэтому признаком не служит.
Номинальные 10–20 А и максимальные 100–400 А файл относит к ВПУ, но у счётчиков прямого
включения те же числа («10(100) А») — поэтому ВПУ определяется только по киловольтам
или прямому названию, иначе каждый «10(100) А» становился бы высоковольтным.

Неизвестный признак **раскрывается** во все допустимые значения: закупка «трёхфазных
счётчиков прямого включения» без слова о креплении подходит под все три крепления, и
фильтр «трёхфазные прямого включения на DIN-рейку» обязан её показать. Но если не
известно ни фазности, ни включения, тип не определён вовсе — пустой список, а не все
одиннадцать: «счётчик электроэнергии» ничего не говорит, и такая закупка попадала бы под
любой фильтр."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.product_form_factor import (
    _PHASES_BY_SERIES,
    MOUNT_DIN,
    MOUNT_PANEL,
    MOUNT_SPLIT,
    _mountings_from_name,
    _mountings_from_text,
)

CONNECTION_DIRECT = "direct"
CONNECTION_SEMI = "semi"  # полукосвенное: через трансформаторы тока, напряжение 230/400 В
CONNECTION_INDIRECT = "indirect"  # косвенное (трансформаторное): ТТ и ТН, 57,7/100 В
HV = "hv"

# Одиннадцать типов из файла «Параметры для ПУ», в его порядке. Код — стабильный
# идентификатор для фильтра и БД, подпись — как в файле.
METER_KINDS: dict[str, str] = {
    "1ph_direct_din": "Однофазный прямого включения на DIN-рейку",
    "1ph_direct_split": "Однофазный прямого включения сплит-исполнения",
    "1ph_direct_panel": "Однофазный прямого включения шкафной",
    "3ph_direct_din": "Трёхфазный прямого включения на DIN-рейку",
    "3ph_direct_split": "Трёхфазный прямого включения сплит-исполнения",
    "3ph_direct_panel": "Трёхфазный прямого включения шкафной",
    "3ph_semi_din": "Трёхфазный полукосвенного включения на DIN-рейку",
    "3ph_indirect_din": "Трёхфазный косвенного включения на DIN-рейку",
    "3ph_semi_panel": "Трёхфазный полукосвенного включения шкафной",
    "3ph_indirect_panel": "Трёхфазный косвенного включения шкафной",
    "hv": "Высоковольтный прибор учёта",
}

_CONNECTIONS_FOR_PHASE = {1: (CONNECTION_DIRECT,), 3: (CONNECTION_DIRECT, CONNECTION_SEMI, CONNECTION_INDIRECT)}
_MOUNTS = (MOUNT_DIN, MOUNT_SPLIT, MOUNT_PANEL)


@dataclass
class MeterFacets:
    """Что удалось определить по тексту. Пустое множество — признак не определён."""

    phases: set[int] = field(default_factory=set)
    connections: set[str] = field(default_factory=set)
    mountings: set[str] = field(default_factory=set)
    hv: bool = False

    @property
    def exact(self) -> bool:
        return self.hv or bool(self.phases and self.connections and self.mountings)


def kind_code(phases: int, connection: str, mounting: str) -> str:
    return f"{phases}ph_{connection}_{mounting}"


def kinds_from_facets(facets: MeterFacets) -> list[str]:
    """Коды типов, совместимых с признаками, в порядке `METER_KINDS`."""

    found: set[str] = set()
    if facets.hv:
        found.add(HV)

    connections = facets.connections
    phases = set(facets.phases)
    # Полукосвенное и косвенное включение бывает только у трёхфазных.
    if not phases and connections and connections <= {CONNECTION_SEMI, CONNECTION_INDIRECT}:
        phases = {3}
    if phases or connections:
        for phase in phases or {1, 3}:
            allowed = _CONNECTIONS_FOR_PHASE[phase]
            for connection in (connections & set(allowed)) if connections else allowed:
                for mounting in facets.mountings or set(_MOUNTS):
                    code = kind_code(phase, connection, mounting)
                    if code in METER_KINDS:
                        found.add(code)
    return [code for code in METER_KINDS if code in found]


def kind_labels(codes: list[str] | None) -> list[str]:
    return [METER_KINDS.get(code) or KIND_GROUPS.get(code, code) for code in codes or []]


# Группа типа — фазность × включение без крепления («3ph_semi_din» → «3ph_semi»): по ней
# сравнивается «делает ли производитель такие приборы вообще».
KIND_GROUPS: dict[str, str] = {
    "1ph_direct": "Однофазный прямого включения",
    "3ph_direct": "Трёхфазный прямого включения",
    "3ph_semi": "Трёхфазный полукосвенного включения",
    "3ph_indirect": "Трёхфазный косвенного включения",
    "hv": "Высоковольтный прибор учёта",
}


def kind_group(code: str) -> str:
    return code if code == HV else code.rsplit("_", 1)[0]


# ------------------------------------------------------------------------ разбор текста

_NUM = r"\d+(?:[.,]\d+)?"

_PHASE_WORDS = (
    (re.compile(r"однофазн|\b1\s*-?\s*фаз|1ф\b", re.IGNORECASE), 1),
    (re.compile(r"тр[её]хфазн|\b3\s*-?\s*фаз|3ф\b", re.IGNORECASE), 3),
)
# «3×230/400 В», «3х57,7/100», «3*(57,7-230)/(100-400)» — трёхфазная запись напряжения.
_THREE_PHASE_VOLTAGE = re.compile(r"\b3\s*[x×х*]\s*\(?\s*(?:57|58|100|127|220|230|240)", re.IGNORECASE)

_CONNECTION_WORDS = (
    # «полукосвенного» проверяется раньше «косвенного» — второе входит в первое.
    (re.compile(r"полу\s*-?\s*косвенн", re.IGNORECASE), CONNECTION_SEMI),
    (re.compile(r"(?<!полу)(?<!полу-)косвенн|трансформаторн\w*\s+включ|включени\w+\s+через\s+(?:измерительн\w+\s+)?трансформатор\w*\s+(?:тока\s+и\s+)?напряжени", re.IGNORECASE), CONNECTION_INDIRECT),
    (re.compile(r"прям\w+\s+(?:\w+\s+)?включ|непосредственн\w+\s+включ", re.IGNORECASE), CONNECTION_DIRECT),
    (re.compile(r"включени\w+\s+через\s+(?:измерительн\w+\s+)?трансформатор\w*\s+тока\b(?!\s+и\s+напряж)", re.IGNORECASE), CONNECTION_SEMI),
)

# Киловольты — только при «номинальном напряжении» / «классе напряжения» или рядом с
# «прибор учёта»: «установка счётчиков в ТП 10 кВ» — закупка обычных счётчиков 0,4 кВ,
# а «рабочее напряжение не менее 10 кВ» стоит у указателя напряжения из той же закупки.
_KV = r"(?:6|10|6\s*[/(]\s*10\)?|6\s*-\s*10|20|35)\s*кВ\b"
_HV_WORDS = re.compile(
    r"высоковольтн|\bВПУ\b|\bПУ\s*ВН\b"
    rf"|(?:номинальн\w*|класс\w*)\s+(?:\w+\s+)?напряжени\w*[^.;\n]{{0,25}}?\b{_KV}"
    rf"|(?:прибор\w*\s+учет\w*|прибор\w*\s+учёт\w*|\bПУ\b|сч[её]тчик\w*)\s+(?:на\s+|класса\s+напряжения\s+)?{_KV}"
    r"|непосредственно\s+на\s+(?:провод|ЛЭП|лини)",
    re.IGNORECASE,
)
_VOLTAGE_57 = re.compile(r"(?<![\d.,])5[78][.,]7(?!\d)|(?<![\d.,])58\s*В\b", re.IGNORECASE)
_VOLTAGE_230 = re.compile(r"(?<![\d.,])(?:220|230|240)\s*(?:/\s*400\s*)?В?\b", re.IGNORECASE)

# «5(60) А», «10 (100) А», «5-60 А», «Iб(Iмакс) = 5(80)».
_CURRENT_PAIR = re.compile(rf"(?<![\d.,])({_NUM})\s*[(/–-]\s*({_NUM})\s*\)?\s*[АA]\b")
# «максимальный ток … 100 А», «Iмакс 10 А».
_CURRENT_MAX = re.compile(
    rf"(?:максимальн\w*\s+(?:сил\w+\s+)?ток\w*|I\s*_?\s*ма[кx]с\w*)[^0-9]{{0,40}}?({_NUM})\s*[АA]\b",
    re.IGNORECASE,
)

_REMOTE_DISPLAY = re.compile(
    r"выносн\w*\s+(?:\w+\s+){0,2}(?:индикатор|дисплей|табло|терминал)|индикаторн\w+\s+устройств|(?:дисплей|индикатор)\w*\s+(?:\w+\s+)?абонент",
    re.IGNORECASE,
)
_SPLIT_EXTRA = re.compile(r"на\s+опор|опор\w*\s+(?:ЛЭП|ВЛ|линии)|на\s+фасад", re.IGNORECASE)


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _max_currents(text: str) -> list[float]:
    values = [_number(m.group(2)) for m in _CURRENT_PAIR.finditer(text)]
    values += [_number(m.group(1)) for m in _CURRENT_MAX.finditer(text)]
    return values


def facets_from_text(text: str | None) -> MeterFacets:
    """Признаки прибора по свободному тексту — наименованию закупки, требованиям ТЗ или
    склеенным характеристикам модели."""

    facets = MeterFacets()
    if not text:
        return facets

    if _HV_WORDS.search(text):
        # ВПУ — отдельный тип, и всё, что сказано рядом о включении, фазах и креплении,
        # описывает его самого (29.09.2026): «высоковольтный прибор учёта непосредственного
        # включения на воздушных линиях 6 кВ» — это ВПУ, который ставится прямо на провод,
        # а не ещё шесть видов обычных счётчиков прямого включения, как выходило раньше.
        # Обычные счётчики в той же закупке находятся по своим позициям (`tender_kinds`).
        facets.hv = True
        return facets

    for pattern, phases in _PHASE_WORDS:
        if pattern.search(text):
            facets.phases.add(phases)
    if _THREE_PHASE_VOLTAGE.search(text):
        facets.phases.add(3)
    # Обозначения серий («СЕ 308», «Меркурий 236», «МИРТЕК-12») — фазность зашита в них;
    # в спецификации закупки прибор часто назван только так.
    # В ТЗ пишут и «СЕ 308», и «СЕ308» — шаблоны серий рассчитаны на слитное написание.
    series_text = re.sub(r"\b([CС][EЕ])\s+(\d{3})", r"\1\2", text)
    for pattern, phases in _PHASES_BY_SERIES:
        if pattern.search(series_text):
            facets.phases.add(phases)

    for pattern, connection in _CONNECTION_WORDS:
        if pattern.search(text):
            facets.connections.add(connection)

    has_57 = bool(_VOLTAGE_57.search(text))
    has_230 = bool(_VOLTAGE_230.search(text))
    # У ВПУ токи «5(100) А» — не признак прямого включения.
    for current in [] if facets.hv else _max_currents(text):
        if 40 <= current <= 120:
            facets.connections.add(CONNECTION_DIRECT)
        elif 5 < current <= 12.5:
            # 10 А: полукосвенное или трансформаторное — выбирает напряжение. Универсальные
            # счётчики «3×(57,7–230)/(100–400) В, 5(10) А» — оба сразу.
            if has_57:
                facets.connections.add(CONNECTION_INDIRECT)
            if has_230 or not has_57:
                facets.connections.add(CONNECTION_SEMI)
            facets.phases.add(3)
    if has_57 and not facets.connections:
        facets.connections.add(CONNECTION_INDIRECT)
        facets.phases.add(3)

    mountings = _mountings_from_text(text) + _mountings_from_name(text)
    if _SPLIT_EXTRA.search(text):
        mountings.append(MOUNT_SPLIT)
    if _REMOTE_DISPLAY.search(text) and not facets.hv:
        mountings.append(MOUNT_SPLIT)
    facets.mountings = set(mountings)
    # Сплит — только прямое включение (в файле нет полукосвенных/косвенных сплитов); если
    # текст одновременно говорит о сплите и о трансформаторном включении, это разные
    # позиции закупки, и раскрытие по всем сочетаниям это корректно отразит.
    return facets


def kinds_from_text(text: str | None) -> list[str]:
    return kinds_from_facets(facets_from_text(text))


# Поля Приложения C, по которым определяется тип модели каталога.
PRODUCT_FIELDS = (
    "Тип прибора",
    "Количество фаз",
    "Номинальное напряжение",
    "Диапазон рабочих напряжений",
    "Номинальный ток",
    "Максимальный ток",
    "Тип включения цепей",
    "Тип монтажа",
    "Тип корпуса",
    "Индикация",
)


def product_facets(
    *,
    model_name: str | None,
    model_code: str | None,
    device_type: str | None,
    characteristics: dict[str, str | None],
) -> MeterFacets:
    """Признаки модели каталога: характеристики с подписью поля (чтобы «Максимальный
    ток: 100 А» разобрался как ток) плюс наименование."""

    from app.services.product_form_factor import classify

    parts = [device_type or "", model_name or "", model_code or ""]
    for name in PRODUCT_FIELDS:
        value = characteristics.get(name)
        if value:
            parts.append(f"{name}: {value}")
    facets = facets_from_text(". ".join(parts))
    if facets.hv:
        # ВПУ — только ВПУ: фазность из серии дала бы ему ещё и типы обычных счётчиков.
        return facets
    form = classify(
        model_name=model_name,
        model_code=model_code,
        registry_modification=None,
        characteristics=characteristics,
    )
    # Фазность и крепление из `product_form_factor` надёжнее разбора склеенного текста:
    # там учтены серии производителей и приоритет характеристики над наименованием.
    if form.phases:
        facets.phases = {form.phases}
    if form.mountings:
        facets.mountings = set(form.mountings)
    return facets


# Упоминание прибора учёта в закупке. Без него «400 В, 3 фазы» и «номинальное напряжение
# 6 кВ» — характеристики сети объекта, а не счётчика (29.09.2026: закупка кабельных
# проходов АП123076 получила все одиннадцать типов, ремонт отопления — шесть, замена
# дверей в общежитии — ВПУ). Серии счётчиков («СЕ 308», «Меркурий 236») тоже считаются.
_METER_MENTION = re.compile(
    r"сч[её]тчик|прибор\w*\s+(?:\w+\s+){0,3}?уч[её]т|\bПУ\b|\bВПУ\b|\bАСКУЭ\b|\bАИИС"
    r"|\bИСУ\b|уч[её]т\w*\s+электр|высоковольтн\w*\s+уч[её]т",
    re.IGNORECASE,
)


def mentions_meter(pieces: list[str]) -> bool:
    for piece in pieces:
        if _METER_MENTION.search(piece):
            return True
        series_text = re.sub(r"\b([CС][EЕ])\s+(\d{3})", r"\1\2", piece)
        if any(pattern.search(series_text) for pattern, _ in _PHASES_BY_SERIES):
            return True
    return False


def tender_kinds(title: str | None, requirement_texts: list[str] | None = None) -> list[str] | None:
    """Типы приборов закупки по наименованию и требованиям к товару. `None` — не
    определено (в БД NULL, а не пустой массив: фильтр по «пусто» не нужен никому)."""

    # Позиции про ВПУ разбираются отдельно и дают только ВПУ; остальные, как и раньше,
    # склеиваются: фазность часто в одном требовании, а крепление — в другом, и тип
    # складывается только из них вместе. Одним текстом ВПУ из наименования «глушил» бы
    # обычные счётчики из требований той же закупки.
    pieces = [piece for piece in [title, *(requirement_texts or [])] if piece]
    if not mentions_meter(pieces):
        return None
    hv = any(_HV_WORDS.search(piece) for piece in pieces)
    rest = "\n".join(piece for piece in pieces if not _HV_WORDS.search(piece))
    kinds = set(kinds_from_text(rest))
    if hv:
        kinds.add(HV)
    return [code for code in METER_KINDS if code in kinds] or None


def fill_tender_kinds(db, tender) -> bool:
    """Пересчитывает `Tender.meter_kinds` по наименованию и требованиям к товару.
    Возвращает True, если значение изменилось."""

    from sqlalchemy import select

    from app.models.analysis import Requirement, RequirementKind

    texts = [
        f"{text or ''} {normalized or ''}"
        for text, normalized in db.execute(
            select(Requirement.text, Requirement.normalized_text).where(
                Requirement.tender_id == tender.id,
                Requirement.kind == RequirementKind.PRODUCT.value,
            )
        )
    ] if tender.id is not None else []
    kinds = tender_kinds(tender.title, texts)
    if kinds == tender.meter_kinds:
        return False
    tender.meter_kinds = kinds
    return True


def product_kinds(product, characteristics) -> list[str]:
    """Типы модели каталога по списку `ProductCharacteristic`."""

    return product_kinds_from_values(
        product, {item.field_name: item.value for item in characteristics if item.value}
    )


def product_kinds_from_values(product, values: dict[str, str | None]) -> list[str]:
    """Типы модели — только если определены фазность и включение (либо это ВПУ).
    Раскрытый «на всякий случай» список типов вводил бы в заблуждение сильнее, чем его
    отсутствие: модель без характеристик не должна выглядеть подходящей под всё."""

    facets = product_facets(
        model_name=product.model_name,
        model_code=product.model_code,
        device_type=product.device_type,
        characteristics=values,
    )
    if not (facets.hv or (facets.phases and facets.connections)):
        return []
    return kinds_from_facets(facets)
