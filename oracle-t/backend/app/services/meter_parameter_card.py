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
from app.services import registry_records_service, upper_software_service
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


@dataclass
class ParameterRow:
    no: int
    name: str
    note: str
    rule: str
    fields: list[ParameterField] = field(default_factory=list)
    facts: list[ParameterFact] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        """Есть ли у модели ответ на параметр: значение хотя бы в одном поле либо
        сведение, которое что-то утверждает (а не «не проверялось»)."""

        return any(item.characteristic and item.characteristic.value for item in self.fields) or any(
            fact.tone != "muted" for fact in self.facts
        )


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
            no=parameter.no, name=parameter.name, note=parameter.note, rule=parameter.rule
        )
        for field_name in parameter.fields:
            group_name = _GROUP_OF_FIELD[field_name]
            row.fields.append(
                ParameterField(group_name, field_name, characteristics.get((group_name, field_name)))
            )

        registry = _REGISTRY_BY_PARAMETER.get(parameter.no)
        if registry and registry in registry_facts:
            row.facts.append(registry_facts[registry])
        if parameter.no == 36:
            row.facts.append(_upper_software_fact(db, product))
        if parameter.rule == RULE_ASTRA:
            row.facts.append(_astra_fact(manufacturer))
        rows.append(row)
    return rows
