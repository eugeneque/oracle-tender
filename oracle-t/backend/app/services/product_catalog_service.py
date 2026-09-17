"""Справочник продукции (раздел 5.3 ТЗ — Этап 4): производители, коды СИ. Разбор
характеристик из документа «Описание типа» и AI-экстракция с сайта производителя —
следующий шаг, не в объёме этой итерации (см. ARCHITECTURE.md)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis import FgisAdapter
from app.adapters.fgis_matching import DeviceKind, classify_si_type, foreign_kind_label
from app.models.log import LogLevel
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ReviewStatus,
    SiType,
    SiTypeSource,
)
from app.models.user import User
from app.schemas.manufacturer import SiTypeOut
from app.seed.characteristics_data import is_known_field
from app.services import si_type_linking
from app.services.audit import log_action


def si_type_to_out(si_type: SiType) -> SiTypeOut:
    return SiTypeOut(
        id=si_type.id,
        manufacturer_id=si_type.manufacturer_id,
        si_code=si_type.si_code,
        notation=si_type.notation,
        type_name=si_type.type_name,
        description_type_url=si_type.description_type_url,
        description_type_version=si_type.description_type_version,
        has_description_type_text=bool(si_type.description_type_text),
        has_allowed_modifications=bool(si_type.allowed_modifications),
        tested_modifications=[m for m in (si_type.tested_modifications or []) if isinstance(m, str)],
        description_type_extracted_version=si_type.description_type_extracted_version,
        description_type_changed_at=si_type.description_type_changed_at,
        mpi_months=si_type.mpi_months,
        valid_to=si_type.valid_to,
        is_actual=si_type.is_actual,
        matched_by=si_type.matched_by,
        source=si_type.source,
        verified_by_user=si_type.verified_by_user,
        review_status=si_type.review_status,
        review_reason=si_type.review_reason,
        last_checked_at=si_type.last_checked_at,
        created_at=si_type.created_at,
        updated_at=si_type.updated_at,
    )


def list_manufacturers(db: Session) -> list[Manufacturer]:
    return list(db.scalars(select(Manufacturer).order_by(Manufacturer.is_mirtek.desc(), Manufacturer.legal_name)))


def get_manufacturer_or_none(db: Session, manufacturer_id: uuid.UUID) -> Manufacturer | None:
    return db.get(Manufacturer, manufacturer_id)


def list_si_types(db: Session, manufacturer_id: uuid.UUID) -> list[SiType]:
    return list(
        db.scalars(
            select(SiType).where(SiType.manufacturer_id == manufacturer_id).order_by(SiType.created_at)
        )
    )


def search_si_types(
    db: Session, manufacturer: Manufacturer, *, actor: User, adapter: FgisAdapter | None = None
) -> list[SiType]:
    """Автопоиск кодов СИ по точному юр. названию производителя (раздел 5.3 ТЗ, п.1
    алгоритма) — стратегия по умолчанию, результат помечается `verified_by_user=False` и
    ждёт проверки человеком (раздел 5.3 ТЗ), не подтверждается автоматически. Повторный
    запуск обновляет уже найденные записи по `(manufacturer_id, si_code)`, а не плодит дубли."""

    adapter = adapter or FgisAdapter()
    found = adapter.search_by_manufacturer(
        manufacturer.legal_name, brand_name=manufacturer.brand_name
    )

    saved: list[SiType] = []
    for result in found:
        if not result.si_code:
            continue  # запись без распознанного кода СИ — нечего сохранять, см. docstring адаптера

        existing = db.scalar(
            select(SiType).where(
                SiType.manufacturer_id == manufacturer.id, SiType.si_code == result.si_code
            )
        )
        if existing is not None:
            if existing.source == SiTypeSource.MANUAL.value or existing.verified_by_user:
                # Ручной ввод/импорт и уже подтверждённые записи имеют приоритет — повторный
                # автопоиск их не перезаписывает (раздел 5.3 ТЗ: "ручной ввод имеет приоритет
                # перед автопоиском при конфликте").
                saved.append(existing)
                continue
            _apply_search_result(existing, result)
            saved.append(existing)
            continue

        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=result.si_code,
            source=SiTypeSource.AUTO_SEARCH.value,
            verified_by_user=False,
        )
        _apply_search_result(si_type, result)
        db.add(si_type)
        saved.append(si_type)

    # Найденные коды сразу подставляются к уже заведённым моделям (`products.si_type_id`).
    # Отдельным шагом это было бы полумерой: автопоиск ради того и запускают, чтобы у моделей
    # появился код СИ, а не чтобы получить второй несвязанный список. Модели, которым тип не
    # нашёлся, остаются как есть — это штатный случай, а не ошибка.
    db.flush()  # без flush новые типы СИ не видны запросу внутри привязки
    link_outcome = si_type_linking.link_products_to_si_types(db, manufacturer, actor_id=actor.id)

    by_brand_only = sum(1 for result in found if result.matched_by == "brand")
    off_scope = sum(1 for si_type in saved if si_type.review_status == ReviewStatus.NEEDS_REVIEW.value)
    log_action(
        db,
        component="product_catalog",
        action=f"search_si_types:{manufacturer.legal_name}",
        result="success" if saved else "empty",
        level=LogLevel.WARNING if off_scope else LogLevel.INFO,
        details=(
            f"Найдено типов СИ: {len(saved)}; из них сопоставлены только по торговому имени "
            f"(требуют особого внимания при проверке): {by_brand_only}; "
            f"не являются счётчиками электрической энергии: {off_scope}; "
            f"привязано к моделям каталога: {link_outcome.linked}"
        ),
        user_id=actor.id,
    )
    db.commit()
    for si_type in saved:
        db.refresh(si_type)
    return saved


def _apply_search_result(si_type: SiType, result) -> None:
    """Переносит данные карточки реестра в запись справочника.

    Уже загруженный текст «Описания типа» и ссылки не затираются пустыми значениями: карточка
    могла не ответить (раздел 5.9 ТЗ — частичный отказ источника не должен обнулять
    накопленные данные)."""

    si_type.notation = result.notation or si_type.notation
    si_type.type_name = result.type_name or si_type.type_name
    si_type.mit_uuid = result.mit_uuid or si_type.mit_uuid
    si_type.description_type_url = result.description_type_url or si_type.description_type_url
    si_type.description_type_mirror_url = (
        result.description_type_mirror_url or si_type.description_type_mirror_url
    )
    si_type.allowed_modifications = result.allowed_modifications or si_type.allowed_modifications
    if getattr(result, "tested_modifications", None):
        si_type.tested_modifications = list(result.tested_modifications)
    si_type.mpi_months = result.mpi_months if result.mpi_months is not None else si_type.mpi_months
    si_type.valid_to = result.valid_to or si_type.valid_to
    si_type.is_actual = result.is_actual if result.is_actual is not None else si_type.is_actual
    si_type.matched_by = result.matched_by or si_type.matched_by
    _flag_if_out_of_scope(si_type)

    # Новая редакция «Описания типа» — признак того, что ранее извлечённый текст устарел
    # и его надо перезагрузить (иначе сопоставление пойдёт по отменённой редакции).
    if result.description_type_version and result.description_type_version != si_type.description_type_version:
        from app.services.fgis_description_ingest import mark_description_changed

        # Дата изменения ставится только у уже известной редакции: у новой записи это не
        # «изменилось», а «впервые загружено».
        if si_type.description_type_version is None:
            si_type.description_type_version = result.description_type_version
            si_type.description_type_text = None
        else:
            mark_description_changed(si_type, new_version=result.description_type_version)


def _flag_if_out_of_scope(si_type: SiType) -> None:
    """Помечает тип СИ, который не является счётчиком электрической энергии (п.1.2 задания).

    Реестр возвращает по производителю всё, что тот когда-либо утверждал: у МИРТЕК из
    28 типов электросчётчиков только половина, остальное — теплосчётчики, счётчики воды и
    газа, УСПД, поверочные установки, трансформаторы тока. Это законные записи производителя,
    поэтому они **не удаляются**: справочник проекта ограничен электросчётчиками (раздел
    2.2.1, 2.2.2 ТТ), но решать судьбу записи должен человек, а не автопоиск.

    Практический смысл пометки — в том, что такие типы исключаются из привязки к моделям
    (`app/services/si_type_linking.py`) и видны в интерфейсе с объяснением, а не сливаются
    с настоящими кандидатами в общий список «требует проверки»."""

    if si_type.verified_by_user or si_type.source == SiTypeSource.MANUAL.value:
        # Человек уже посмотрел на эту запись и оставил её — автоматика не вправе
        # переводить её обратно в «требует проверки» (раздел 5.3 ТЗ: ручной ввод и
        # подтверждение имеют приоритет над автопоиском).
        return

    kind = classify_si_type(si_type.type_name, si_type.notation)
    if kind is DeviceKind.ELECTRICITY_METER:
        return

    foreign = foreign_kind_label(si_type.type_name, si_type.notation)
    si_type.review_status = ReviewStatus.NEEDS_REVIEW.value
    si_type.review_reason = (
        f"Тип СИ «{si_type.type_name or si_type.notation or si_type.si_code}» — "
        + (f"это {foreign}, а не счётчик электрической энергии" if foreign
           else "вид измерений не распознан как «счётчик электрической энергии»")
        + ". Справочник продукции ограничен электросчётчиками (раздел 2.2.2 ТТ); "
        "запись сохранена, но к моделям каталога не привязывается."
    )


def update_si_type(db: Session, si_type: SiType, *, si_code: str | None, verified_by_user: bool | None, actor: User) -> SiType:
    changed: list[str] = []
    if si_code is not None and si_code != si_type.si_code:
        si_type.si_code = si_code
        si_type.source = SiTypeSource.MANUAL.value  # ручная правка — приоритет над автопоиском
        changed.append("si_code")
    if verified_by_user is not None and verified_by_user != si_type.verified_by_user:
        si_type.verified_by_user = verified_by_user
        changed.append("verified_by_user")

    if changed:
        log_action(
            db,
            component="product_catalog",
            action=f"update_si_type:{si_type.id}",
            result="success",
            level=LogLevel.INFO,
            details=f"Изменены поля: {', '.join(changed)}",
            user_id=actor.id,
        )
        db.commit()
        db.refresh(si_type)
    return si_type


def list_products(db: Session, manufacturer_id: uuid.UUID) -> list[Product]:
    return list(
        db.scalars(
            select(Product).where(Product.manufacturer_id == manufacturer_id).order_by(Product.model_name)
        )
    )


def create_product(
    db: Session,
    manufacturer: Manufacturer,
    *,
    model_name: str,
    si_type_id: uuid.UUID | None,
    article: str | None,
    device_type: str | None,
    actor: User,
) -> Product:
    product = Product(
        manufacturer_id=manufacturer.id,
        si_type_id=si_type_id,
        model_name=model_name,
        article=article,
        device_type=device_type,
    )
    db.add(product)

    # Код СИ подставляется сам, если подходящий уже найден автопоиском: заставлять человека
    # после каждой добавленной модели жать «Привязать к моделям» — лишний шаг, о котором он
    # к тому же не догадается. Явно переданный `si_type_id` при этом в приоритете —
    # `link_product` не трогает уже заполненное поле.
    linked = si_type_linking.link_product(db, product)

    log_action(
        db,
        component="product_catalog",
        action="create_product",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"{manufacturer.legal_name} → {model_name}"
            + (f"; код СИ подставлен автоматически: {linked.si_code}" if linked is not None and si_type_id is None else "")
        ),
        user_id=actor.id,
    )
    db.commit()
    db.refresh(product)
    return product


def update_product(db: Session, product: Product, *, fields: dict[str, object], actor: User) -> Product:
    """`fields` — только реально присланные поля (PATCH-семантика, см. `model_fields_set`
    в вызывающем эндпоинте)."""

    for name, value in fields.items():
        setattr(product, name, value)

    if fields:
        log_action(
            db,
            component="product_catalog",
            action=f"update_product:{product.id}",
            result="success",
            level=LogLevel.INFO,
            details=f"Изменены поля: {', '.join(sorted(fields))}",
            user_id=actor.id,
        )
        db.commit()
        db.refresh(product)
    return product


def upsert_characteristic_manually(
    db: Session,
    product: Product,
    *,
    group_name: str,
    field_name: str,
    value: str,
    actor: User,
) -> ProductCharacteristic:
    """Ручной ввод/правка характеристики (раздел 5.3 ТЗ, источник 3). Всегда помечается
    `manual_entry` + `verified_by_user=True` — такое значение не перетирается повторной
    AI-экстракцией (см. `characteristic_extraction._save_characteristic`)."""

    if not is_known_field(group_name, field_name):
        raise ValueError(
            f"Поле «{group_name} → {field_name}» отсутствует в справочнике характеристик "
            "(Приложение C ТЗ)"
        )

    existing = db.scalar(
        select(ProductCharacteristic).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.group_name == group_name,
            ProductCharacteristic.field_name == field_name,
        )
    )
    if existing is None:
        existing = ProductCharacteristic(
            product_id=product.id, group_name=group_name, field_name=field_name
        )
        db.add(existing)

    existing.value = value
    existing.source = CharacteristicSource.MANUAL_ENTRY.value
    existing.confidence = None  # ручной ввод — не вероятностная оценка
    existing.verified_by_user = True

    log_action(
        db,
        component="product_catalog",
        action=f"upsert_characteristic:{product.id}",
        result="success",
        level=LogLevel.INFO,
        details=f"{group_name} → {field_name}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(existing)
    return existing


def set_characteristic_verified(
    db: Session, characteristic: ProductCharacteristic, *, verified: bool, actor: User
) -> ProductCharacteristic:
    """Подтверждение человеком автоматически извлечённого значения (раздел 5.3 ТЗ —
    «с обязательной проверкой человеком перед сохранением в каталог»)."""

    characteristic.verified_by_user = verified
    log_action(
        db,
        component="product_catalog",
        action=f"verify_characteristic:{characteristic.id}",
        result="success",
        level=LogLevel.INFO,
        details=f"{characteristic.group_name} → {characteristic.field_name}: verified={verified}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(characteristic)
    return characteristic


def fetch_description_type(
    db: Session, si_type: SiType, *, actor: User, adapter: FgisAdapter | None = None
) -> SiType:
    """Скачивает и разбирает документ «Описание типа» по сохранённой ссылке (раздел 5.3 ТЗ,
    п.2 алгоритма). Ленивая операция — по требованию из карточки СИ, не при автопоиске (тот
    же принцип, что и загрузка документов тендера на Этапе 3)."""

    if not si_type.description_type_url and not si_type.description_type_mirror_url:
        log_action(
            db,
            component="product_catalog",
            action=f"fetch_description_type:{si_type.id}",
            result="skipped",
            level=LogLevel.WARNING,
            details="Нет ссылки на «Описание типа» — сначала нужен автопоиск или ручной ввод ссылки",
            user_id=actor.id,
        )
        db.commit()
        return si_type

    adapter = adapter or FgisAdapter()
    text = adapter.fetch_description_type_text(
        si_type.description_type_url or "", mirror_url=si_type.description_type_mirror_url
    )
    si_type.description_type_text = text

    log_action(
        db,
        component="product_catalog",
        action=f"fetch_description_type:{si_type.id}",
        result="success" if text else "error",
        level=LogLevel.INFO if text else LogLevel.WARNING,
        details=None if text else "Текст не извлечён (см. предупреждения адаптера ФГИС в логе)",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(si_type)
    return si_type
