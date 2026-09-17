"""Исполнения приборов из карточек Аршина → записи каталога (замечание заказчика 15.09.2026).

**Что решается.** Обход сайта производителя видит только выложенное в каталоге. Реестр
узнаёт о новом исполнении раньше: у НАРТИС-И100 корпус W115 появился в редакции 2 «Описания
типа» (приказ № 1037 от 01.06.2026) — в карточке типа он стоит среди «представленных на
испытания», — а в каталоге на сайте Нартиса до сих пор только W112 и W113. Пока такого
исполнения нет в справочнике, тендер на него получит «нет данных», хотя реестр всё уже
рассказал.

**Как.** Из карточки типа берутся полные условные обозначения представленных на испытания
исполнений (`si_types.tested_modifications`, разобраны в `app/adapters/fgis.py`), из
каждого вычленяется короткое обозначение — тип плюс первый сегмент («НАРТИС-И100-W115»).
Если в каталоге производителя нет модели с таким обозначением, она заводится с источником
`fgis` и пометкой «требует проверки»: наименование здесь составлено из названия типа, а
не взято у производителя, и человек должен это видеть. Если модель есть, но без кода СИ, —
привязывается к типу.

Полное обозначение сохраняется в `products.registry_modification`: в нём закодированы
характеристики (корпус, ток, интерфейсы), и вместе с легендой структуры обозначения из той
же карточки оно расшифровывается моделью (`fgis_description_ingest`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis import execution_designation
from app.models.log import LogLevel
from app.models.manufacturer import (
    Manufacturer,
    Product,
    ProductDataSource,
    ProductStatus,
    ReviewStatus,
    SiType,
)
from app.services import si_type_linking
from app.services.audit import log_action

COMPONENT = "catalog_sync"

# Начало пометки на записи, заведённой из реестра. По нему пометка узнаётся и снимается,
# когда исполнение появляется на сайте производителя и запись подхватывает обход каталога.
REGISTRY_REASON_PREFIX = "Исполнение заведено из реестра ФГИС"


@dataclass
class ModificationsOutcome:
    si_types_scanned: int = 0
    modifications_seen: int = 0
    products_created: int = 0
    products_linked: int = 0
    already_known: int = 0
    created_names: list[str] = field(default_factory=list)


def discover_modifications(
    db: Session,
    manufacturer: Manufacturer,
    *,
    actor_id: uuid.UUID | None = None,
    si_types: list[SiType] | None = None,
) -> ModificationsOutcome:
    """Сверяет исполнения из карточек типов производителя с каталогом.

    Берутся только типы-электросчётчики (`electricity_meter_types`): у производителей в
    реестре есть и теплосчётчики, и поверочные установки, а справочник ограничен счётчиками
    электроэнергии."""

    outcome = ModificationsOutcome()
    candidates = si_types if si_types is not None else si_type_linking.electricity_meter_types(db, manufacturer.id)
    products = list(db.scalars(select(Product).where(Product.manufacturer_id == manufacturer.id)))

    for si_type in candidates:
        modifications = [m for m in (si_type.tested_modifications or []) if isinstance(m, str)]
        if not modifications:
            continue
        outcome.si_types_scanned += 1
        for full in modifications:
            outcome.modifications_seen += 1
            short = execution_designation(full, si_type.notation)
            if not short:
                continue
            existing = _find_product(products, short)
            if existing is not None:
                outcome.already_known += 1
                if existing.registry_modification is None:
                    existing.registry_modification = full
                if existing.si_type_id is None:
                    existing.si_type_id = si_type.id
                    outcome.products_linked += 1
                    si_type_linking._clear_unlinked_flag(existing)
                continue
            product = _create_product(db, manufacturer, si_type, short=short, full=full)
            products.append(product)
            outcome.products_created += 1
            outcome.created_names.append(short)

    if outcome.products_created or outcome.products_linked:
        log_action(
            db,
            component=COMPONENT,
            action=f"registry_modifications:{manufacturer.id}",
            result="success",
            level=LogLevel.INFO,
            details=(
                f"Исполнения из реестра ФГИС: заведено {outcome.products_created} "
                f"({', '.join(outcome.created_names[:10])}), привязано к типам "
                f"{outcome.products_linked}, уже известны {outcome.already_known}"
            ),
            user_id=actor_id,
        )
    db.commit()
    return outcome


def _find_product(products: list[Product], short: str) -> Product | None:
    """Модель каталога с таким обозначением исполнения.

    Совпадение — префиксное в обе стороны по тем же ключам, что и привязка к типам СИ:
    «НАРТИС-И100-W112» из реестра и «НАРТИС‑И100-W112» (с неразрывным дефисом) с сайта
    должны сойтись, как и «CE102M» латиницей с «СЕ102М» кириллицей. Из нескольких подходящих
    берётся самая короткая по коду — она и есть само исполнение, а не его вариант с опциями."""

    keys = si_type_linking.designation_keys(short)
    if not keys:
        return None
    found: list[tuple[int, Product]] = []
    for product in products:
        for source in (product.model_code, product.article, product.model_name):
            product_keys = si_type_linking.designation_keys(source)
            if any(pk == k or pk.startswith(k) for pk in product_keys for k in keys):
                found.append((min(len(pk) for pk in product_keys), product))
                break
    if not found:
        return None
    return min(found, key=lambda pair: pair[0])[1]


def _create_product(
    db: Session, manufacturer: Manufacturer, si_type: SiType, *, short: str, full: str
) -> Product:
    product = Product(
        manufacturer_id=manufacturer.id,
        si_type_id=si_type.id,
        # Наименование — из названия типа в реестре: «Счетчики электроэнергии однофазные
        # интеллектуальные НАРТИС-И100-W115». Как называет исполнение сам производитель,
        # станет известно, когда его найдёт обход сайта или поиск документации.
        model_name=f"{si_type.type_name or 'Прибор'} {short}"[:255],
        model_code=short[:100],
        device_type=_device_type(si_type.type_name),
        registry_modification=full,
        data_source=ProductDataSource.FGIS.value,
        status=ProductStatus.ACTIVE.value,
        review_status=ReviewStatus.NEEDS_REVIEW.value,
        review_reason=(
            f"{REGISTRY_REASON_PREFIX}: тип {si_type.si_code} «{si_type.notation}», редакция "
            f"«Описания типа» {si_type.description_type_version or '—'}; на сайте "
            f"производителя исполнение не найдено. Полное обозначение: {full}"
        ),
    )
    db.add(product)
    db.flush()
    logger.info(f"Каталог: из реестра ФГИС заведено исполнение «{short}» ({si_type.si_code})")
    return product


def _device_type(type_name: str | None) -> str | None:
    lowered = (type_name or "").lower()
    if "трехфаз" in lowered or "трёхфаз" in lowered:
        return "Трёхфазный счётчик электроэнергии"
    if "однофаз" in lowered:
        return "Однофазный счётчик электроэнергии"
    return "Счётчик электроэнергии" if lowered else None
