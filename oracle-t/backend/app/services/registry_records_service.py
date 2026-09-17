"""Реестры допуска продукции: ПП 719 (ГИСП), ЗАК ПАО «Россети», реестр российского ПО
(замечание тестировщика 16.09.2026: «предусмотреть возможность проверки наличия
реестровой записи в реестре российской промышленной продукции и действующего ЗАК
Россетей»).

Проверка устроена в три шага, как и интеграция в ПО верхнего уровня:

1. **Записи** (`upsert_record`) — у модели каталога по каждому реестру одна запись: номер,
   дата выдачи, срок действия, ссылка на карточку в реестре, либо явное «проверено: записи
   нет». Заводятся вручную: оба реестра закрыты для роботов (см. `SourceType.ADMISSION_REGISTRY`).
2. **Состояние** (`record_state`) — считается кодом по датам на сегодня: действует /
   истекает (меньше `EXPIRING_DAYS` дней) / истекла / отсутствует. Слово «действующее» в
   ТЗ означает именно это, и решать по нему должна арифметика дат, а не модель.
3. **Факты** (`registry_facts`) — когда в требованиях закупки упомянут реестр, в карточку
   производителя для сопоставления добавляются строки `[registry]`: по каким моделям
   запись есть и до какого числа действует, по каким — проверено, что нет. Требования о
   допуске оцениваются только по этим фактам (см. `_SYSTEM_PROMPT` в
   `compliance_service`): истекшая или отсутствующая запись — `not_meets`, отсутствие
   сведений — `no_data`.

Записи хранятся у модели, а не у производителя: ЗАК выдаётся на конкретные типы приборов
(«МИРТЕК-32-РУ» аттестован, «МИРТЕК-1» — нет), и запись «у производителя есть ЗАК» ничего
не говорила бы о модели, которую предлагают в закупку.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product
from app.models.registry_record import AdmissionRegistry, ProductRegistryRecord, RegistryPresence
from app.models.source import Source
from app.models.tender import Tender
from app.models.user import User
from app.services.audit import log_action

COMPONENT = "registry_records"

# За сколько дней до конца срока запись считается «истекающей». Квартал — потому что
# столько в среднем идёт закупка от подачи заявки до поставки: ЗАК, истекающее через месяц,
# к моменту поставки уже не действует.
EXPIRING_DAYS = 90


@dataclass(frozen=True)
class RegistryInfo:
    key: str
    label: str
    official_name: str
    # Ключ источника в `sources` — оттуда берётся официальная ссылка.
    source_key: str
    patterns: tuple[re.Pattern[str], ...]


# Как реестры называют в ТЗ. Шаблоны нарочно широкие: одну и ту же вещь пишут «реестр
# промышленной продукции», «реестр Минпромторга», «ПП 719», «ГИСП», «постановление № 719».
# «Аттестация» без «Россетей» тоже учитывается: у Россетей и их ДЗО в ТЗ она стоит
# как «заключение аттестационной комиссии» без названия компании.
REGISTRIES: dict[str, RegistryInfo] = {
    AdmissionRegistry.INDUSTRIAL_PRODUCTS.value: RegistryInfo(
        key=AdmissionRegistry.INDUSTRIAL_PRODUCTS.value,
        label="Реестр промпродукции (ПП 719)",
        official_name="Реестр промышленной продукции, произведённой на территории РФ (ПП РФ № 719, ГИСП)",
        source_key="reg_gisp",
        patterns=(
            re.compile(r"реестр\w*\s+(российск\w+\s+)?промышленн\w+\s+продукци", re.IGNORECASE),
            re.compile(r"реестр\w*\s+минпромторг", re.IGNORECASE),
            re.compile(r"\bГИСП\b", re.IGNORECASE),
            re.compile(r"(постановлени\w*|ПП)\s*(Правительства\s*(РФ)?\s*)?(№\s*)?719\b", re.IGNORECASE),
            re.compile(r"№\s*719\b"),
        ),
    ),
    AdmissionRegistry.ROSSETI_ATTESTATION.value: RegistryInfo(
        key=AdmissionRegistry.ROSSETI_ATTESTATION.value,
        label="ЗАК ПАО «Россети»",
        official_name="Заключение аттестационной комиссии ПАО «Россети» (реестр аттестованного оборудования)",
        source_key="reg_rosseti_zak",
        patterns=(
            re.compile(r"\bЗАК\b"),
            re.compile(r"аттестац\w+\s+комисси", re.IGNORECASE),
            re.compile(r"аттестован\w*\s+(в\s+)?(ПАО\s+)?[«\"]?Россет", re.IGNORECASE),
            re.compile(r"аттестац\w+\s+(ПАО\s+)?[«\"]?Россет", re.IGNORECASE),
            re.compile(r"перечн\w+\s+оборудовани\w+[^.]{0,80}допущенн\w+[^.]{0,40}Россет", re.IGNORECASE),
            re.compile(r"допущен\w+\s+(ПАО\s+)?[«\"]?Россет", re.IGNORECASE),
        ),
    ),
    AdmissionRegistry.SOFTWARE_REGISTRY.value: RegistryInfo(
        key=AdmissionRegistry.SOFTWARE_REGISTRY.value,
        label="Реестр российского ПО",
        official_name="Единый реестр российских программ для ЭВМ и баз данных (Минцифры России)",
        source_key="",
        patterns=(
            re.compile(r"реестр\w*\s+российск\w+\s+программ", re.IGNORECASE),
            re.compile(r"реестр\w*\s+(отечественн\w+|российск\w+)\s+ПО\b", re.IGNORECASE),
            re.compile(r"реестр\w*\s+программ\s+для\s+(электронных\s+вычислительных\s+машин|ЭВМ)", re.IGNORECASE),
            re.compile(r"реестр\w*\s+минцифры", re.IGNORECASE),
        ),
    ),
}

STATE_ACTIVE = "active"
STATE_EXPIRING = "expiring"
STATE_EXPIRED = "expired"
STATE_ABSENT = "absent"
STATE_UNKNOWN = "unknown"

STATE_LABELS: dict[str, str] = {
    STATE_ACTIVE: "действует",
    STATE_EXPIRING: "истекает",
    STATE_EXPIRED: "истекла",
    STATE_ABSENT: "записи нет",
    STATE_UNKNOWN: "не проверялось",
}


# ---------------------------------------------------------------------------- определение


def detect_registries(text: str | None) -> list[str]:
    """Какие реестры упомянуты в тексте требования, в порядке `REGISTRIES`."""

    if not text:
        return []
    found = []
    for key, info in REGISTRIES.items():
        if any(pattern.search(text) for pattern in info.patterns):
            found.append(key)
    return found


def registries_for_requirements(requirements: list[Requirement]) -> dict[str, list[Requirement]]:
    """Реестр → требования, в которых он упомянут. Пусто — в закупке о допуске не спрашивают."""

    result: dict[str, list[Requirement]] = {}
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        for key in detect_registries(text):
            result.setdefault(key, []).append(requirement)
    return result


# ------------------------------------------------------------------------------ состояние


def record_state(record: ProductRegistryRecord | None, today: date | None = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    if record is None:
        return STATE_UNKNOWN
    if record.presence == RegistryPresence.ABSENT.value:
        return STATE_ABSENT
    if record.valid_to is None:
        return STATE_ACTIVE
    if record.valid_to < today:
        return STATE_EXPIRED
    if record.valid_to - today <= timedelta(days=EXPIRING_DAYS):
        return STATE_EXPIRING
    return STATE_ACTIVE


def _format_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def describe_record(record: ProductRegistryRecord, today: date | None = None) -> str:
    """Одна строка о записи для человека и для карточки производителя в сопоставлении."""

    state = record_state(record, today)
    if state == STATE_ABSENT:
        return "проверено: записи в реестре нет"
    parts = []
    if record.record_number:
        parts.append(f"№ {record.record_number}")
    if record.issued_at:
        parts.append(f"от {_format_date(record.issued_at)}")
    if record.valid_to:
        parts.append(f"до {_format_date(record.valid_to)}")
    else:
        parts.append("срок действия не ограничен или не указан")
    parts.append(f"({STATE_LABELS[state]})")
    return " ".join(parts)


# --------------------------------------------------------------------------------- записи


def list_records(db: Session, product_id: uuid.UUID) -> list[ProductRegistryRecord]:
    return list(
        db.scalars(
            select(ProductRegistryRecord)
            .where(ProductRegistryRecord.product_id == product_id)
            .order_by(ProductRegistryRecord.registry)
        )
    )


def get_record(db: Session, product_id: uuid.UUID, registry: str) -> ProductRegistryRecord | None:
    return db.scalar(
        select(ProductRegistryRecord).where(
            ProductRegistryRecord.product_id == product_id,
            ProductRegistryRecord.registry == registry,
        )
    )


def upsert_record(
    db: Session,
    product: Product,
    registry: str,
    *,
    presence: str,
    record_number: str | None,
    issued_at: date | None,
    valid_to: date | None,
    url: str | None,
    note: str | None,
    verified: bool,
    actor: User | None,
) -> ProductRegistryRecord:
    """Заводит или обновляет запись по паре «модель × реестр». Для `absent` номер и даты
    обнуляются: запись «нет в реестре» с номером — противоречие, которое потом не
    объяснить."""

    if registry not in REGISTRIES:
        raise ValueError(f"Неизвестный реестр: {registry}")
    if presence not in {item.value for item in RegistryPresence}:
        raise ValueError(f"Неизвестный признак наличия: {presence}")
    if presence == RegistryPresence.ABSENT.value:
        record_number, issued_at, valid_to = None, None, None
    elif not (record_number or "").strip():
        raise ValueError("Для записи «есть в реестре» нужен реестровый номер")
    if issued_at and valid_to and valid_to < issued_at:
        raise ValueError("Срок действия не может заканчиваться раньше даты выдачи")

    record = get_record(db, product.id, registry)
    if record is None:
        record = ProductRegistryRecord(product_id=product.id, registry=registry)
        db.add(record)
    record.presence = presence
    record.record_number = (record_number or "").strip() or None
    record.issued_at = issued_at
    record.valid_to = valid_to
    record.url = (url or "").strip() or None
    record.note = (note or "").strip() or None
    record.verified_by_user = verified
    record.verified_at = datetime.now(timezone.utc) if verified else None
    record.updated_by_id = actor.id if actor else None
    db.flush()

    log_action(
        db,
        component=COMPONENT,
        action=f"upsert:{product.model_name}:{registry}",
        result="success",
        details=describe_record(record),
        user_id=actor.id if actor else None,
    )
    db.commit()
    db.refresh(record)
    return record


def delete_record(db: Session, record: ProductRegistryRecord, *, actor: User | None) -> None:
    db.delete(record)
    log_action(
        db,
        component=COMPONENT,
        action=f"delete:{record.product_id}:{record.registry}",
        result="success",
        user_id=actor.id if actor else None,
    )
    db.commit()


# --------------------------------------------------------------------------------- сводки


def _source_urls(db: Session) -> dict[str, str]:
    keys = [info.source_key for info in REGISTRIES.values() if info.source_key]
    rows = db.execute(select(Source.key, Source.url).where(Source.key.in_(keys))).all()
    return {key: url for key, url in rows if url}


def list_registries(db: Session) -> list[dict]:
    urls = _source_urls(db)
    return [
        {
            "key": info.key,
            "label": info.label,
            "official_name": info.official_name,
            "url": urls.get(info.source_key),
        }
        for info in REGISTRIES.values()
    ]


def record_out(record: ProductRegistryRecord, today: date | None = None) -> dict:
    return {
        "id": record.id,
        "product_id": record.product_id,
        "registry": record.registry,
        "presence": record.presence,
        "record_number": record.record_number,
        "issued_at": record.issued_at,
        "valid_to": record.valid_to,
        "url": record.url,
        "note": record.note,
        "verified_by_user": record.verified_by_user,
        "verified_at": record.verified_at,
        "state": record_state(record, today),
        "summary": describe_record(record, today),
        "updated_at": record.updated_at,
    }


def product_overview(db: Session, product: Product) -> list[dict]:
    """Для карточки модели: по каждому реестру — запись или её отсутствие."""

    records = {record.registry: record for record in list_records(db, product.id)}
    urls = _source_urls(db)
    rows = []
    for info in REGISTRIES.values():
        record = records.get(info.key)
        rows.append(
            {
                "registry": info.key,
                "label": info.label,
                "official_name": info.official_name,
                "url": urls.get(info.source_key),
                "state": record_state(record),
                "record": record_out(record) if record else None,
            }
        )
    return rows


def manufacturer_records(
    db: Session, manufacturer: Manufacturer
) -> list[tuple[Product, ProductRegistryRecord]]:
    rows = db.execute(
        select(Product, ProductRegistryRecord)
        .join(ProductRegistryRecord, ProductRegistryRecord.product_id == Product.id)
        .where(Product.manufacturer_id == manufacturer.id)
        .order_by(Product.model_name, ProductRegistryRecord.registry)
    ).all()
    return [(product, record) for product, record in rows]


# Порядок «лучшего» состояния для сводки по производителю: одна действующая запись важнее
# десяти истёкших — в закупку предлагают именно ту модель, у которой допуск есть.
_STATE_RANK = {
    STATE_ACTIVE: 0,
    STATE_EXPIRING: 1,
    STATE_EXPIRED: 2,
    STATE_ABSENT: 3,
    STATE_UNKNOWN: 4,
}


def manufacturer_summary(
    db: Session, manufacturer: Manufacturer, today: date | None = None
) -> dict[str, dict]:
    """Реестр → лучшее состояние среди моделей производителя и сами модели с записями."""

    grouped: dict[str, list[dict]] = {key: [] for key in REGISTRIES}
    for product, record in manufacturer_records(db, manufacturer):
        grouped[record.registry].append(
            {
                "product_id": product.id,
                "model_name": product.model_name,
                "state": record_state(record, today),
                "summary": describe_record(record, today),
                "record_number": record.record_number,
                "valid_to": record.valid_to,
                "url": record.url,
            }
        )
    summary: dict[str, dict] = {}
    for key, products in grouped.items():
        products.sort(key=lambda item: (_STATE_RANK[item["state"]], item["model_name"]))
        summary[key] = {
            "state": products[0]["state"] if products else STATE_UNKNOWN,
            "products": products,
        }
    return summary


# ----------------------------------------------------------------------------------- факты


def registry_facts(
    db: Session, manufacturer: Manufacturer, requirements: list[Requirement]
) -> list[str]:
    """Строки `[registry]` для карточки производителя — только по реестрам, о которых
    спрашивает закупка, и только там, где есть что сказать.

    «Записи не заведено» строкой не становится: это `no_data`, и модель получит его сама
    по отсутствию факта. А вот «проверено: записи нет» и «истекла …» — факты, и они
    должны быть в карточке, потому что дают `not_meets`."""

    asked = registries_for_requirements(requirements)
    if not asked:
        return []

    lines: list[str] = []
    records = manufacturer_records(db, manufacturer)
    for key in asked:
        info = REGISTRIES[key]
        matching = [(product, record) for product, record in records if record.registry == key]
        if not matching:
            continue
        for product, record in matching:
            lines.append(f"[registry] {product.model_name}: {info.label} — {describe_record(record)}")
    return lines


def tender_overview(db: Session, tender: Tender) -> dict:
    """Сводка для карточки закупки: какие требования о допуске, какие реестры упомянуты и
    как каждый производитель в них представлен."""

    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender.id)
            .order_by(Requirement.created_at)
        )
    )
    asked = registries_for_requirements(requirements)
    requirement_rows: list[dict] = []
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        found = detect_registries(text)
        if found:
            requirement_rows.append(
                {
                    "id": requirement.id,
                    "text": requirement.text,
                    "criticality": requirement.criticality,
                    "registries": found,
                }
            )

    registries = []
    for row in list_registries(db):
        row["mentioned"] = row["key"] in asked
        registries.append(row)

    manufacturers = []
    if asked:
        for manufacturer in db.scalars(
            select(Manufacturer).order_by(Manufacturer.is_mirtek.desc(), Manufacturer.legal_name)
        ):
            manufacturers.append(
                {
                    "manufacturer_id": manufacturer.id,
                    "name": manufacturer.brand_name or manufacturer.legal_name,
                    "is_mirtek": manufacturer.is_mirtek,
                    "registries": manufacturer_summary(db, manufacturer),
                }
            )

    return {
        "requirements": requirement_rows,
        "mentioned_registries": list(asked.keys()),
        "registries": registries,
        "manufacturers": manufacturers,
    }
