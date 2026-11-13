"""Карточка модели по 39 параметрам файла тендерного отдела «Параметры для ПУ» (28.09.2026).

Тендерный отдел сверяет ТЗ с прибором по своей таблице, а карточка модели до этого была
разложена только по группам Приложения C — искать ответ на «П21 невыпадающие винты» среди
158 полей было неудобно, а у части параметров места в справочнике не было вовсе. Здесь те
же данные собираются в порядке файла:

* значения характеристик — по полям `MeterParameter.fields` (пустое поле тоже в ответе:
  человеку видно, что заполнить, и значение вписывается прямо из этого вида);
* П35 (ПП 719) и П38 (ЗАК Россетей) — из реестров допуска модели: это не свойство прибора,
  а запись с датами (`registry_records_service`);
* П36 (ПО верхнего уровня) — вдобавок к полям совместимости, списки поддерживаемого
  оборудования площадок (`upper_software_service`);
* П39 (Astra Linux) — каталог «Ready for Astra» по производителю, как в сопоставлении;
* П26 (трёхпозиционное реле) — по комментарию файла такое реле бывает только у Энергомеры,
  Тайпита и Пульсара, у моделей остальных производителей параметр не выводится.

Ничего нового не хранится: вид собирается из уже существующих данных на каждый запрос.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic
from app.models.registry_record import AdmissionRegistry
from app.seed.characteristics_data import ALL_FIELDS
from app.seed.meter_parameters import (
    METER_PARAMETERS,
    RULE_ASTRA,
    RULE_ONLY_MANUFACTURERS,
    THREE_POSITION_RELAY_BRANDS,
)
from app.services import (
    characteristic_values,
    product_form_factor,
    registry_records_service,
    software_integration_service,
    upper_software_service,
)
from app.services.characteristic_extraction import list_characteristics

# Параметры, ответ на которые лежит в реестре допуска, а не в характеристиках.
_REGISTRY_BY_PARAMETER = {
    35: AdmissionRegistry.INDUSTRIAL_PRODUCTS.value,
    38: AdmissionRegistry.ROSSETI_ATTESTATION.value,
}

# Состояние записи в реестре → тон пометки в карточке.
_REGISTRY_TONE = {
    registry_records_service.STATE_ACTIVE: "ok",
    registry_records_service.STATE_EXPIRING: "warn",
    registry_records_service.STATE_EXPIRED: "bad",
    registry_records_service.STATE_ABSENT: "bad",
    registry_records_service.STATE_UNKNOWN: "muted",
}

# Поле справочника → его группа. Имя поля ищется по первой группе: одноимённые поля в
# разных группах («Методика поверки») параметрам не нужны.
_GROUP_OF_FIELD: dict[str, str] = {}
for _group, _field in ALL_FIELDS:
    _GROUP_OF_FIELD.setdefault(_field, _group)


# Подписи полей справочника в карточке параметров. Названия Приложения C точны для
# справочника, но в строке параметра читаются странно: «Тип отсчётного устройства» под П32
# (ЖКИ) тендерный отдел принял за опечатку (замечание 13.10.2026, п.6).
FIELD_LABELS: dict[str, str] = {
    "Тип отсчётного устройства": "Тип индикатора (ЖКИ / электромеханический)",
    "Индикация": "Индикатор",
    "Автономное питание часов": "Батарейка часов",
    "Контроль вскрытия": "Электронные пломбы",
}

# Откуда обычно берётся ответ на параметр — для подсказки у пустой строки «нет данных —
# опросите источник» (замечание 13.10.2026: пользователь должен понимать, что сведений не
# хватает потому, что источник не опрошен, а не потому, что у прибора этого нет).
SOURCE_SITE = "site"
SOURCE_FGIS = "fgis"
SOURCE_MANUAL = "manual"
SOURCE_GISP = "gisp"
SOURCE_ZAK = "zak"
SOURCE_UPPER = "upper_software"
SOURCE_ASTRA = "astra"

_SOURCES_BY_PARAMETER: dict[int, tuple[str, ...]] = {
    **{no: (SOURCE_SITE, SOURCE_FGIS) for no in (1, 2, 4, 5, 6, 7, 8, 9, 12)},
    **{no: (SOURCE_SITE, SOURCE_MANUAL, SOURCE_FGIS) for no in (3, 10, 11)},
    **{no: (SOURCE_SITE, SOURCE_MANUAL) for no in (14, 15, 16, 37)},
    **{no: (SOURCE_MANUAL, SOURCE_FGIS) for no in (18, 32)},
    **{no: (SOURCE_MANUAL,) for no in (13, 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 33, 34)},
    35: (SOURCE_GISP,),
    36: (SOURCE_UPPER, SOURCE_MANUAL),
    38: (SOURCE_ZAK,),
    39: (SOURCE_ASTRA,),
}


@dataclass
class ParameterFact:
    """Сведение не из характеристик: реестр, списки ПО, каталог Astra, правило файла.
    `tone`: ok — подтверждает, bad — опровергает, warn — требует внимания, muted — нет данных."""

    text: str
    tone: str = "muted"
    url: str | None = None


@dataclass
class ParameterField:
    group_name: str
    field_name: str
    characteristic: ProductCharacteristic | None = None
    label: str = ""
    # Значение по исполнениям корпуса: [{"keys": ["W1"], "value": "192×122×57 мм"}, …]. Пусто —
    # значение одно на модель (05.11.2026).
    variants: list[dict] = field(default_factory=list)
    # Значение для корпуса этой модели из перечня серии (12.11.2026, см. `_model_value`).
    shown_value: str | None = None
    vague: bool = False


@dataclass
class ParameterRow:
    no: int
    name: str
    note: str
    rule: str
    fields: list[ParameterField] = field(default_factory=list)
    facts: list[ParameterFact] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        """Есть ли у модели ответ на параметр: значение хотя бы в одном поле либо
        сведение, которое что-то утверждает (а не «не проверялось»)."""

        return any(
            item.characteristic and item.characteristic.value and not item.vague for item in self.fields
        ) or any(fact.tone != "muted" for fact in self.facts)


def _registry_facts(db: Session, product: Product) -> dict[str, ParameterFact]:
    facts: dict[str, ParameterFact] = {}
    for row in registry_records_service.product_overview(db, product):
        state = row["state"]
        label = registry_records_service.STATE_LABELS.get(state, state)
        record = row["record"]
        text = f"{row['label']}: {record['summary']}" if record else f"{row['label']}: {label}"
        facts[row["registry"]] = ParameterFact(
            text=text,
            tone=_REGISTRY_TONE.get(state, "muted"),
            url=(record or {}).get("url") or row["url"],
        )
    return facts


def _upper_software_fact(db: Session, product: Product) -> ParameterFact:
    names = []
    for row in upper_software_service.product_support(db, product):
        if row["name"] not in names:
            names.append(row["name"])
    if not names:
        return ParameterFact("В списках поддерживаемого оборудования площадок модели нет")
    return ParameterFact("В списках ПО верхнего уровня: " + ", ".join(names), tone="ok")


def _astra_fact(manufacturer: Manufacturer | None) -> ParameterFact:
    from app.adapters.astra_compatible import PAGE_URL, load_catalog

    if manufacturer is None:
        return ParameterFact("Производитель модели не указан")
    catalog = load_catalog()
    if catalog is None:
        return ParameterFact("Каталог «Ready for Astra» сейчас недоступен", url=PAGE_URL)
    brand = manufacturer.brand_name or manufacturer.legal_name
    found = catalog.find(brand)
    if not found:
        return ParameterFact(
            f"В каталоге «Ready for Astra» ПО производителя «{brand}» не найдено",
            tone="bad",
            url=PAGE_URL,
        )
    return ParameterFact(
        "«Ready for Astra»: " + "; ".join(item.describe() for item in found), tone="ok", url=PAGE_URL
    )


def _has_three_position_relay(manufacturer: Manufacturer | None) -> bool:
    if manufacturer is None:
        return False
    brand = (manufacturer.brand_name or manufacturer.legal_name or "").lower()
    return any(name in brand for name in THREE_POSITION_RELAY_BRANDS)


def _housing_variants(characteristic: ProductCharacteristic | None) -> list[dict]:
    """Значение, записанное перечнем по корпусам («192×122×57 (W1); 182×125×55 (W2)»), → список
    вариантов, чтобы карточка показывала размеры выбранного корпуса, а не всех сразу
    (замечание 05.11.2026: «Габариты» подтягивали параметры со всех корпусов). Меньше двух
    вариантов с ключом корпуса — значение одно, делить нечего."""

    value = characteristic.value if characteristic else None
    if not value:
        return []
    keyed = characteristic_values._segments_with_keys(value)
    if len(keyed) < 2:
        return []
    return [{"keys": sorted(keys), "value": text} for keys, text in keyed]


# Оговорка без значения: «в зависимости от типа корпуса», «зависит от исполнения».
_VAGUE_RE = re.compile(
    r"^\s*(?:в\s+зависимост\w*|завис\w*)\s+от\s+(?:тип\w*\s+)?(?:корпус\w*|исполнени\w*|модификаци\w*)\s*\.?\s*$",
    re.IGNORECASE,
)


def _model_value(product: Product, item: ParameterField) -> None:
    """Значение поля для этой модели, а не для серии (замечание 12.11.2026).

    В карточке МИРТЕК-12-РУ-D17 «Способ крепления» был «на щиток, DIN-рейку, опору ЛЭП», а
    «Размеры» — «в зависимости от типа корпуса» или размеры всех корпусов сразу: значения
    пришли из «Описания типа» / руководства на весь тип. Код корпуса в обозначении модели
    (D17) выбирает свой вариант — тем же правилом, что и при записи характеристик
    (`product_form_factor.model_specific_value`, `characteristic_values.resolve_family_value`).
    Хранимое значение не меняется: правка в строке по-прежнему правит всё поле."""

    value = item.characteristic.value if item.characteristic else None
    if not value:
        return
    if _VAGUE_RE.match(value):
        item.vague = True
        return
    narrowed = product_form_factor.model_specific_value(
        item.field_name,
        value,
        model_code=product.model_code,
        model_name=product.model_name,
        family_source=False,
    )
    if narrowed and narrowed != value:
        item.shown_value = narrowed
        return
    # «W1, W2, D17, SP17» — перечень корпусов серии; у модели с кодом корпуса — он сам.
    code = product_form_factor.body_code(product.model_code, product.model_name)
    if item.field_name == product_form_factor.FIELD_BODY and code:
        listed = set(re.findall(r"[A-Za-zА-Яа-я]{1,3}\d{1,3}", value))
        if len(listed) > 1 and code in listed:
            item.shown_value = code
            return
    if not characteristic_values.is_family_level(value):
        return
    tokens = characteristic_values.designation_tokens(
        product.model_code, product.model_name, product.execution, product.registry_modification
    )
    picked = characteristic_values.resolve_family_value(value, tokens)
    if picked:
        item.shown_value = picked
        item.variants = []


def _single_card_field(parameter, fields: list[ParameterField]) -> list[ParameterField]:
    """Для параметров с одним ответом (`card_fields`) — одно поле без подписи: первое
    заполненное, а если пусто всё — первое, чтобы администратору было куда вписать."""

    shown = [item for item in fields if item.field_name in parameter.card_fields]
    filled = [item for item in shown if item.characteristic and item.characteristic.value and not item.vague]
    return (filled or shown)[:1]


def _specialist_fact(db: Session, product: Product) -> ParameterFact | None:
    """П36: интеграции, подтверждённые специалистом МИРТЕК (раздел 82), — их карточка
    показывала только в шапке, а строка параметра брала лишь публичные списки площадок, и у
    каждой модели линейки не хватало своих систем (замечание 12.11.2026)."""

    rows = software_integration_service.product_integrations(db, product)
    if not rows:
        return None
    names = [row["name"] + ("" if row["has_mirtek_verdict"] == "yes" else " (частично)") for row in rows]
    return ParameterFact("Подтверждено специалистом МИРТЕК: " + ", ".join(names), tone="ok")


def product_parameters(db: Session, product: Product) -> list[ParameterRow]:
    characteristics = {
        (item.group_name, item.field_name): item for item in list_characteristics(db, product.id)
    }
    manufacturer = db.get(Manufacturer, product.manufacturer_id) if product.manufacturer_id else None
    registry_facts = _registry_facts(db, product)
    relay_applies = _has_three_position_relay(manufacturer)

    rows: list[ParameterRow] = []
    for parameter in METER_PARAMETERS:
        # П26: трёхпозиционное реле по файлу бывает только у Энергомеры, Тайпита и
        # Пульсара — у остальных строка «у вас такого нет» в каждой карточке была бы шумом
        # (замечание 28.09.2026), параметр не выводится.
        if parameter.rule == RULE_ONLY_MANUFACTURERS and not relay_applies:
            continue
        row = ParameterRow(
            no=parameter.no,
            name=parameter.name,
            note=parameter.note,
            rule=parameter.rule,
            sources=list(_SOURCES_BY_PARAMETER.get(parameter.no, ())),
        )
        for field_name in parameter.fields:
            group_name = _GROUP_OF_FIELD[field_name]
            item = ParameterField(
                group_name,
                field_name,
                characteristics.get((group_name, field_name)),
                label=FIELD_LABELS.get(field_name, field_name),
                variants=_housing_variants(characteristics.get((group_name, field_name))),
            )
            _model_value(product, item)
            row.fields.append(item)
        if parameter.card_fields:
            row.fields = _single_card_field(parameter, row.fields)

        registry = _REGISTRY_BY_PARAMETER.get(parameter.no)
        if registry and registry in registry_facts:
            row.facts.append(registry_facts[registry])
        if parameter.no == 36:
            specialist = _specialist_fact(db, product)
            if specialist:
                row.facts.append(specialist)
            row.facts.append(_upper_software_fact(db, product))
        if parameter.rule == RULE_ASTRA:
            row.facts.append(_astra_fact(manufacturer))
        rows.append(row)
    return rows
