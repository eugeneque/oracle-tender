"""Наведение порядка в уже накопленных характеристиках (кнопка администратора, 13.10.2026).

Правки этого дня (`characteristic_values`, приоритет источников, расшифровка обозначения
только у исполнений из реестра) защищают новые данные, но на сервере уже лежат значения,
записанные по старым правилам: «Количество фаз: 2», «Класс точности: A1R1», «Максимальный
ток: Iмакс», габариты всего семейства, расшифровка чужого образца в шапке модели, перечни
сайта в «Вне справочника». Перезапускать ради этого обучение по Аршину — платить за
обращения к модели повторно и не вернуть значения сайта, которые оно затёрло.

Ремонт работает **без ИИ** и в два шага:

1. **Чистка базы** (секунды, в запросе): прежние значения проверяются теми же правилами,
   что и новые, — приводятся к формату, выбирается вариант своей модели, негодное
   удаляется; чужие обозначения исполнений снимаются; перечни «вне справочника»
   раскладываются по полям. Подтверждённое человеком и введённое вручную не трогается.
2. **Восстановление с сайтов** (минуты, в фоне): у производителей, где удалялись значения
   «Описания типа», сайт обходится заново целиком и без ИИ — значения сайта по рангу выше и
   встают на место затёртых.

Сначала вызывается предпросмотр (`apply=False`) — администратор видит, сколько и что
изменится, и только потом применяет.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.log import LogLevel
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
)
from app.models.user import User
from app.seed.characteristics_data import resolve_field
from app.services import catalog_queue_service, characteristic_values
from app.services.audit import log_action
from app.services.registry_modifications import modification_belongs_to

COMPONENT = "catalog_repair"
MAX_EXAMPLES = 30


@dataclass
class RepairOutcome:
    applied: bool = False
    values_normalised: int = 0
    values_removed: int = 0
    family_values_resolved: int = 0
    modifications_cleared: int = 0
    extra_fields_spread: int = 0
    extra_entries_removed: int = 0
    products_affected: int = 0
    manufacturers_to_refresh: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def note(self, product: Product, text: str) -> None:
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append(f"{product.model_code or product.model_name}: {text}")


def _repair_value(
    row: ProductCharacteristic, tokens: set[str], designation: str | None
) -> tuple[str | None, str]:
    """Новое значение и причина. `(row.value, "")` — менять нечего; `(None, причина)` — удалить."""

    value = row.value or ""
    from_fgis = row.source == CharacteristicSource.FGIS_DESCRIPTION_TYPE.value
    if from_fgis and row.ai_extracted and characteristic_values.looks_like_designation_code(value, designation, row.field_name):
        return None, "код из условного обозначения"
    normalised = characteristic_values.normalise_value(row.field_name, value)
    if normalised is None:
        return None, "негодное значение"
    if characteristic_values.is_family_level(normalised):
        picked = characteristic_values.resolve_family_value(normalised, tokens)
        if picked is not None:
            picked = characteristic_values.normalise_value(row.field_name, picked)
            if picked:
                return picked, "вариант своей модели из перечня семейства"
        if from_fgis:
            return None, "перечень всего семейства"
    if normalised != value:
        return normalised, "приведено к формату"
    return value, ""


def _spread_extras(db: Session, product: Product, values: dict, outcome: RepairOutcome, apply: bool) -> bool:
    extras = dict(product.extra_specifications or {})
    if not extras:
        return False
    remaining: dict[str, str] = {}
    changed = False
    for key, raw in extras.items():
        value = str(raw)
        if characteristic_values.looks_like_designation_code(value, product.registry_modification):
            outcome.extra_entries_removed += 1
            outcome.note(product, f"«вне справочника» {key}: «{value}» — удалено (код обозначения)")
            changed = True
            continue
        split = characteristic_values.split_feature_list(key, value)
        targets: list[tuple[str, str]] | None = None
        if split:
            targets, leftover = split
            if leftover:
                remaining[key] = leftover
        else:
            duplicate = characteristic_values.DUPLICATE_FIELDS.get(key.strip().casefold())
            targets = [(duplicate, value)] if duplicate else None
        if not targets:
            remaining[key] = value
            continue
        changed = True
        if key not in remaining:
            outcome.extra_entries_removed += 1
        for field_name, field_value in targets:
            resolved = resolve_field("", field_name)
            if resolved is None:
                continue
            existing = values.get(resolved)
            if existing is not None and existing.value:
                continue
            outcome.extra_fields_spread += 1
            outcome.note(product, f"«вне справочника» {key} → {resolved[1]}: «{field_value}»")
            if apply:
                if existing is None:
                    existing = ProductCharacteristic(
                        product_id=product.id,
                        group_name=resolved[0],
                        field_name=resolved[1],
                        verified_by_user=False,
                    )
                    db.add(existing)
                    values[resolved] = existing
                existing.value = field_value
                existing.source = CharacteristicSource.MANUFACTURER_SITE.value
                existing.confidence = None
                existing.ai_extracted = False
    if changed and apply:
        product.extra_specifications = remaining
    return changed


def repair(db: Session, *, apply: bool, actor: User) -> RepairOutcome:
    """Предпросмотр (`apply=False`, база не меняется) либо применение ремонта."""

    outcome = RepairOutcome(applied=apply)
    manufacturers = {m.id: m for m in db.scalars(select(Manufacturer))}
    refresh: set[uuid.UUID] = set()

    for product in db.scalars(select(Product).order_by(Product.model_name)):
        touched = False
        tokens = characteristic_values.designation_tokens(product.model_code, product.model_name, product.execution)
        designation = product.registry_modification
        rows = list(db.scalars(select(ProductCharacteristic).where(ProductCharacteristic.product_id == product.id)))
        values = {(row.group_name, row.field_name): row for row in rows}

        for row in rows:
            if row.verified_by_user or row.source == CharacteristicSource.MANUAL_ENTRY.value or not row.value:
                continue
            new_value, reason = _repair_value(row, tokens, designation)
            if new_value == row.value:
                continue
            touched = True
            if new_value is None:
                outcome.values_removed += 1
                outcome.note(product, f"{row.field_name}: «{row.value[:80]}» — удалено ({reason})")
                if row.source == CharacteristicSource.FGIS_DESCRIPTION_TYPE.value:
                    refresh.add(product.manufacturer_id)
                if apply:
                    db.delete(row)
                    values.pop((row.group_name, row.field_name), None)
                continue
            if reason.startswith("вариант"):
                outcome.family_values_resolved += 1
            else:
                outcome.values_normalised += 1
            outcome.note(product, f"{row.field_name}: «{row.value[:60]}» → «{new_value[:60]}» ({reason})")
            if apply:
                row.value = new_value

        if designation and not modification_belongs_to(product, designation):
            touched = True
            outcome.modifications_cleared += 1
            outcome.note(product, f"снято чужое обозначение исполнения «{designation}»")
            if apply:
                product.registry_modification = None

        if _spread_extras(db, product, values, outcome, apply):
            touched = True

        if touched:
            outcome.products_affected += 1

    outcome.manufacturers_to_refresh = sorted(
        (manufacturers[mid].brand_name or manufacturers[mid].legal_name) for mid in refresh if mid in manufacturers
    )

    if not apply:
        db.rollback()
        return outcome

    log_action(
        db,
        component=COMPONENT,
        action="repair_characteristics",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"Моделей затронуто {outcome.products_affected}: приведено к формату "
            f"{outcome.values_normalised}, выбран вариант модели {outcome.family_values_resolved}, "
            f"удалено {outcome.values_removed}, снято чужих обозначений "
            f"{outcome.modifications_cleared}, «вне справочника» разложено "
            f"{outcome.extra_fields_spread} / убрано {outcome.extra_entries_removed}; сайты к "
            f"повторному обходу: {', '.join(outcome.manufacturers_to_refresh) or 'нет'}"
        ),
        user_id=actor.id,
    )
    db.commit()
    if refresh:
        _refresh_sites_in_background(sorted(refresh), actor.id)
    return outcome


def _refresh_sites_in_background(manufacturer_ids: list[uuid.UUID], actor_id: uuid.UUID) -> None:
    """Повторный полный обход сайтов без ИИ — в потоке очереди справочника, после того, что
    уже в ней стоит: обход нагружает чужой сайт и не должен идти параллельно другому."""

    def work() -> None:
        from app.services import catalog_autofill, catalog_site_sync

        db = SessionLocal()
        try:
            actor = db.get(User, actor_id)
            for manufacturer_id in manufacturer_ids:
                manufacturer = db.get(Manufacturer, manufacturer_id)
                if manufacturer is None:
                    continue
                adapter = catalog_autofill._site_adapter_for(db, manufacturer)
                if adapter is None:
                    continue
                try:
                    outcome = catalog_site_sync.sync_catalog(
                        db,
                        manufacturer=manufacturer,
                        adapter=adapter,
                        actor=actor,
                        use_ai=False,
                        full_refresh=True,
                    )
                    logger.info(
                        f"Ремонт каталога: сайт «{manufacturer.legal_name}» обойдён заново — "
                        f"характеристик {outcome.characteristics_saved}"
                    )
                except Exception as exc:  # noqa: BLE001 - сбой одного сайта не мешает остальным
                    db.rollback()
                    logger.warning(f"Ремонт каталога: обход сайта «{manufacturer.legal_name}» не удался: {exc}")
        finally:
            db.close()

    catalog_queue_service.submit(work)
