"""Исполнитель очереди для источника ФГИС (раздел 4.2, 5.3 ТЗ; задача 1 задания).

Что здесь есть и почему именно так:

**Два триггера, один код.** Ревалидация по расписанию и поиск по событию отличаются только
`reason` задачи и тем, какая цель в ней указана (`si_type_id` против `model_name`). Дальше
оба идут через `handle_task` → `FgisAdapter` → дисамбигуация → запись в справочник. Держать
для них два пути значило бы поддерживать две копии логики обращения к реестру (п.1.1
задания прямо это запрещает).

**Дисамбигуация обязательна.** Результат поиска никогда не сохраняется «как есть»: сначала
кандидаты проходят через `app/adapters/fgis_matching.py`, который отсекает приборы не того
вида измерений и требует проверки человеком при неоднозначности. Без этого фильтра поиск по
торговой марке «Пульсар» подкладывал в справочник «Описание типа» пожарного извещателя
вместо счётчика электроэнергии — то есть модуль сопоставления начинал сверять требования
тендера с характеристиками чужого прибора (п.1.2 задания).

**Ревалидация проверяет не «изменилось ли что-нибудь», а три конкретных факта:** цел ли
номер в Госреестре, не вышла ли новая редакция «Описания типа» и не истёк ли срок действия
свидетельства об утверждении типа. Последнее особенно важно для тендера: прибор с истёкшим
утверждением типа формально нельзя поставить, и система должна показать это до подачи
заявки, а не после.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis import FgisAdapter
from app.adapters.fgis_matching import CandidateDecision, DeviceKind, pick_candidate
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason, CatalogQueueStatus
from app.models.log import LogLevel
from app.models.manufacturer import (
    Manufacturer,
    Product,
    ReviewStatus,
    SiType,
    SiTypeSource,
)
from app.services import catalog_queue_service, registry_modifications, si_type_linking
from app.services.audit import log_action
from app.services.catalog_queue_service import TaskOutcome
from app.services.fgis_description_ingest import mark_description_changed

ADAPTER_KEY = "fgis"
COMPONENT = "catalog_sync"

# Насколько заранее предупреждать об истекающем свидетельстве об утверждении типа. Полгода —
# не круглое число ради красоты: цикл «заметили → подали заявку в Росстандарт → получили
# новое свидетельство» занимает месяцы, и предупреждение за неделю уже бесполезно.
EXPIRY_WARNING_DAYS = 180


def register() -> None:
    catalog_queue_service.register_handler(ADAPTER_KEY, handle_task)


# --- Постановка задач ---


def enqueue_missing_model(
    db: Session,
    *,
    model_name: str,
    manufacturer: Manufacturer | None = None,
    product: Product | None = None,
    actor_id: uuid.UUID | None = None,
) -> CatalogLookupTask | None:
    """Триггер по событию (п.1.1 задания): модуль сопоставления встретил модель без данных.

    Запрос уходит в реестр немедленно, вне суточного расписания, — иначе в первый раз, когда
    по этой модели придёт тендер, система ответит «информация отсутствует» и человек будет
    искать характеристики руками, хотя они есть в открытом реестре."""

    return catalog_queue_service.enqueue(
        db,
        adapter_key=ADAPTER_KEY,
        reason=CatalogQueueReason.MISSING_CATALOG_DATA,
        model_name=model_name,
        manufacturer_id=manufacturer.id if manufacturer else (product.manufacturer_id if product else None),
        product_id=product.id if product else None,
        actor_id=actor_id,
    )


def enqueue_revalidation(db: Session, *, older_than_hours: int = 24, limit: int = 100) -> int:
    """Триггер по расписанию (п.1.1 задания): ставит в очередь давно не проверявшиеся типы СИ.

    Берутся только записи автопоиска: ручной ввод и импорт имеют приоритет перед автопоиском
    (раздел 5.3 ТЗ), и перезапрашивать их из реестра, чтобы потом не иметь права перезаписать,
    значило бы зря ходить в ФГИС.

    `older_than_hours` отсекает уже проверенные в этом цикле — иначе при ежедневном прогоне
    очередь наполнялась бы повторно теми же записями, которые обработчик не успел взять
    вчера (`DEFAULT_BATCH_SIZE` меньше, чем весь справочник).
    """

    threshold = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    stale = list(
        db.scalars(
            select(SiType)
            .where(
                SiType.source == SiTypeSource.AUTO_SEARCH.value,
                (SiType.last_checked_at.is_(None)) | (SiType.last_checked_at < threshold),
            )
            # Никогда не проверявшиеся — первыми: у них ещё нет ни МПИ, ни срока действия.
            .order_by(SiType.last_checked_at.is_not(None), SiType.last_checked_at)
            .limit(limit)
        )
    )

    queued = 0
    for si_type in stale:
        task = catalog_queue_service.enqueue(
            db,
            adapter_key=ADAPTER_KEY,
            reason=CatalogQueueReason.SCHEDULED_REVALIDATION,
            si_type_id=si_type.id,
            manufacturer_id=si_type.manufacturer_id,
            model_name=si_type.notation,
            # Пачку не запускаем по каждой записи: обработчик всё равно один, и `run_now`
            # на сотне записей означал бы сотню лишних сабмитов в пул.
            run_now=False,
        )
        if task is not None and task.status == CatalogQueueStatus.QUEUED.value:
            queued += 1
    return queued


# --- Выполнение задач ---


def handle_task(db: Session, task: CatalogLookupTask) -> TaskOutcome:
    """Единая точка выполнения для обоих триггеров."""

    adapter = FgisAdapter()
    if task.si_type_id is not None:
        si_type = db.get(SiType, task.si_type_id)
        if si_type is None:
            return TaskOutcome(message="Код СИ удалён из справочника — задача неактуальна")
        return _revalidate(db, si_type, adapter=adapter)
    return _lookup_model(db, task, adapter=adapter)


def _lookup_model(db: Session, task: CatalogLookupTask, *, adapter: FgisAdapter) -> TaskOutcome:
    """Поиск типа СИ по модели, которой нет в справочнике."""

    manufacturer = (
        db.get(Manufacturer, task.manufacturer_id) if task.manufacturer_id is not None else None
    )
    if manufacturer is None:
        return TaskOutcome(message="Производитель не указан — искать в реестре не по чему")

    candidates = adapter.search_by_manufacturer(
        manufacturer.legal_name, brand_name=manufacturer.brand_name, fetch_cards=False
    )
    decision = pick_candidate(
        candidates, expected_kind=DeviceKind.ELECTRICITY_METER, model_name=task.model_name
    )

    if not decision.has_result:
        return _review_outcome(db, decision, task=task, manufacturer=manufacturer)

    # Карточка типа запрашивается только для принятого кандидата: `enrich_from_card` — это
    # отдельный запрос к ФГИС на каждую запись, и делать их для отвергнутых вариантов значит
    # умножать нагрузку на нестабильный сервис без пользы.
    result = adapter.enrich_from_card(decision.accepted)
    si_type = _upsert_si_type(db, manufacturer, result)

    if si_type is not None:
        # Новый тип СИ подставляется всем подходящим моделям производителя, а не только той,
        # из-за которой запрос попал в очередь: реестр отдаёт тип на всё семейство исполнений
        # («МИРТЕК-12-РУ» покрывает и D17, и SP17, и W9), и оставлять остальные без кода
        # значило бы гонять один и тот же запрос к ФГИС по разу на каждое исполнение.
        # Уже привязанные модели при этом не трогаются (раздел 5.3 ТЗ).
        db.flush()
        si_type_linking.link_products_to_si_types(db, manufacturer)

    db.commit()
    return TaskOutcome(
        message=(
            f"Тип СИ {result.si_code} «{result.type_name or '—'}» сопоставлен с моделью "
            f"«{task.model_name or '—'}»"
        )
    )


def _revalidate(db: Session, si_type: SiType, *, adapter: FgisAdapter) -> TaskOutcome:
    """Ревалидация сохранённой карточки типа: актуальность, версия документа, срок действия."""

    si_type.last_checked_at = datetime.now(timezone.utc)

    if not si_type.mit_uuid:
        # Без идентификатора карточки перезапросить нечего — такие записи приходят из ручного
        # ввода/импорта и ревалидируются только после автопоиска по производителю.
        db.commit()
        return TaskOutcome(
            message=f"Тип СИ {si_type.si_code}: нет идентификатора карточки ФГИС, ревалидация пропущена"
        )

    before = (si_type.description_type_version, si_type.is_actual, si_type.valid_to)
    result = adapter.enrich_from_card(_as_search_result(si_type))

    changes: list[str] = []
    new_modifications: list[str] = []
    if result.description_type_version and result.description_type_version != si_type.description_type_version:
        changes.append(
            f"новая редакция «Описания типа» {si_type.description_type_version or '—'} → "
            f"{result.description_type_version}"
        )
        # Текст прежней редакции больше не описывает актуальный тип — его надо перезагрузить,
        # иначе сопоставление пойдёт по отменённой редакции. Дата изменения нужна интерфейсу
        # и еженедельному обучению справочника (`fgis_description_ingest`), которое
        # перечитает документ и обновит характеристики привязанных моделей.
        mark_description_changed(si_type, new_version=result.description_type_version)
    if result.description_type_url:
        si_type.description_type_url = result.description_type_url
    if result.description_type_mirror_url:
        si_type.description_type_mirror_url = result.description_type_mirror_url
    if result.allowed_modifications:
        si_type.allowed_modifications = result.allowed_modifications
    if result.tested_modifications:
        # Новые исполнения в реестре — то самое «изменение в описании типа», о котором
        # заказчик просил узнавать: у НАРТИС-И100 корпус W115 появился именно так, раньше,
        # чем на сайте производителя.
        known = {_modification_key(m) for m in (si_type.tested_modifications or []) if isinstance(m, str)}
        new_modifications = [m for m in result.tested_modifications if _modification_key(m) not in known]
        if new_modifications:
            changes.append(f"новые исполнения в реестре: {', '.join(new_modifications)}")
        si_type.tested_modifications = list(result.tested_modifications)
    if result.mpi_months is not None and result.mpi_months != si_type.mpi_months:
        changes.append(f"МПИ {si_type.mpi_months or '—'} → {result.mpi_months} мес.")
        si_type.mpi_months = result.mpi_months
    if result.valid_to is not None and result.valid_to != si_type.valid_to:
        changes.append(f"срок действия {si_type.valid_to or '—'} → {result.valid_to}")
        si_type.valid_to = result.valid_to
    if result.is_actual is not None and result.is_actual != si_type.is_actual:
        changes.append(f"актуальность {si_type.is_actual} → {result.is_actual}")
        si_type.is_actual = result.is_actual

    warning = _expiry_warning(si_type)
    if warning:
        si_type.review_status = ReviewStatus.NEEDS_REVIEW.value
        si_type.review_reason = warning

    db.commit()

    if new_modifications:
        # Исполнение из реестра сразу заводится в каталог (с пометкой «требует проверки»),
        # а его характеристики и документация подтянутся ближайшим проходом обучения.
        manufacturer = db.get(Manufacturer, si_type.manufacturer_id)
        if manufacturer is not None:
            outcome = registry_modifications.discover_modifications(
                db, manufacturer, si_types=[si_type]
            )
            if outcome.products_created:
                changes.append(
                    f"в каталог заведено исполнений: {', '.join(outcome.created_names)}"
                )

    if not changes and not warning:
        return TaskOutcome(message=f"Тип СИ {si_type.si_code}: изменений нет")

    log_action(
        db,
        component=COMPONENT,
        action=f"revalidate_si_type:{si_type.si_code}",
        result="changed" if changes else "warning",
        level=LogLevel.WARNING if warning else LogLevel.INFO,
        details="; ".join(changes + ([warning] if warning else [])),
    )
    db.commit()
    logger.info(f"ФГИС: ревалидация {si_type.si_code} (было {before}): {'; '.join(changes) or 'без изменений'}")
    return TaskOutcome(
        message="; ".join(changes + ([warning] if warning else [])),
        needs_review=bool(warning),
    )


def _expiry_warning(si_type: SiType) -> str | None:
    """Текст предупреждения о свидетельстве об утверждении типа, если оно истекло или
    истекает. `None` — всё в порядке либо срок в реестре не указан (бессрочные типы)."""

    if si_type.is_actual is False:
        return (
            f"Тип СИ {si_type.si_code} помечен в реестре как неактуальный — прибор нельзя "
            "предлагать в закупку до выяснения"
        )
    if si_type.valid_to is None:
        return None
    days_left = (si_type.valid_to - date.today()).days
    if days_left < 0:
        return (
            f"Свидетельство об утверждении типа {si_type.si_code} истекло {si_type.valid_to} — "
            "прибор нельзя предлагать в закупку"
        )
    if days_left <= EXPIRY_WARNING_DAYS:
        return (
            f"Свидетельство об утверждении типа {si_type.si_code} истекает {si_type.valid_to} "
            f"(осталось {days_left} дн.)"
        )
    return None


def _as_search_result(si_type: SiType):
    """Карточка справочника → объект, который понимает `FgisAdapter.enrich_from_card`.

    Импорт внутри функции, а не в шапке модуля: `SiSearchResult` нужен только здесь, а на
    уровне модуля он лишний раз связал бы сервис с внутренним типом адаптера."""

    from app.adapters.fgis import SiSearchResult

    return SiSearchResult(
        si_code=si_type.si_code,
        type_name=si_type.type_name,
        notation=si_type.notation,
        mit_uuid=si_type.mit_uuid,
        matched_by=si_type.matched_by or "legal",
    )


def _upsert_si_type(db: Session, manufacturer: Manufacturer, result) -> SiType | None:
    """Сохраняет принятого кандидата. Повторный запуск обновляет запись по
    `(manufacturer_id, si_code)`, а не плодит дубли."""

    if not result.si_code:
        return None

    si_type = db.scalar(
        select(SiType).where(
            SiType.manufacturer_id == manufacturer.id, SiType.si_code == result.si_code
        )
    )
    if si_type is None:
        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=result.si_code,
            source=SiTypeSource.AUTO_SEARCH.value,
            verified_by_user=False,
        )
        db.add(si_type)
    elif si_type.source == SiTypeSource.MANUAL.value or si_type.verified_by_user:
        # Ручной ввод и подтверждённые человеком записи автопоиском не перезаписываются
        # (раздел 5.3 ТЗ).
        si_type.last_checked_at = datetime.now(timezone.utc)
        return si_type

    si_type.notation = result.notation or si_type.notation
    si_type.type_name = result.type_name or si_type.type_name
    si_type.mit_uuid = result.mit_uuid or si_type.mit_uuid
    si_type.description_type_url = result.description_type_url or si_type.description_type_url
    si_type.description_type_mirror_url = (
        result.description_type_mirror_url or si_type.description_type_mirror_url
    )
    si_type.allowed_modifications = result.allowed_modifications or si_type.allowed_modifications
    si_type.mpi_months = result.mpi_months if result.mpi_months is not None else si_type.mpi_months
    si_type.valid_to = result.valid_to or si_type.valid_to
    si_type.is_actual = result.is_actual if result.is_actual is not None else si_type.is_actual
    si_type.matched_by = result.matched_by or si_type.matched_by
    si_type.last_checked_at = datetime.now(timezone.utc)
    si_type.review_status = ReviewStatus.OK.value
    si_type.review_reason = None

    if result.tested_modifications:
        si_type.tested_modifications = list(result.tested_modifications)

    if (
        result.description_type_version
        and result.description_type_version != si_type.description_type_version
    ):
        if si_type.description_type_version is None:
            si_type.description_type_version = result.description_type_version
            si_type.description_type_text = None
        else:
            mark_description_changed(si_type, new_version=result.description_type_version)

    db.flush()
    return si_type


def _modification_key(value: str) -> str:
    """Ключ сравнения исполнений: без регистра, пробелов и вида дефиса."""

    return "".join(ch for ch in value.lower() if not ch.isspace() and ch not in "-–—")


def _review_outcome(
    db: Session,
    decision: CandidateDecision,
    *,
    task: CatalogLookupTask,
    manufacturer: Manufacturer,
) -> TaskOutcome:
    """Неоднозначный результат: ничего не сохраняем в справочник, но фиксируем всё, что
    видели, — и в журнале (WARNING со списком кандидатов, п.1.2 задания), и в самой задаче,
    чтобы человек мог разобраться, не повторяя поиск.

    Если задача пришла от конкретной модели каталога, пометка ставится и на неё: иначе
    «требует проверки» останется только в очереди, а в карточке товара человек увидит просто
    пустые характеристики без объяснения."""

    if not decision.needs_review:
        # Выдача реестра пуста — это не неоднозначность, а штатное «типа СИ нет».
        return TaskOutcome(
            message=(
                f"В реестре ФГИС нет типов СИ для «{manufacturer.legal_name}» "
                f"по модели «{task.model_name or '—'}»"
            )
        )

    details = {
        "reason": decision.reason,
        "candidates": [
            {
                "si_code": c.si_code,
                "type_name": c.type_name,
                "manufacturer_name": c.manufacturer_name,
                "matched_by": getattr(c, "matched_by", None),
            }
            for c in decision.considered
        ],
        "rejected": [
            {
                "si_code": item.si_code,
                "type_name": item.type_name,
                "manufacturer_name": item.manufacturer_name,
                "reason": item.reason,
            }
            for item in decision.rejected
        ],
    }

    if task.product_id is not None:
        product = db.get(Product, task.product_id)
        if product is not None:
            product.review_status = ReviewStatus.NEEDS_REVIEW.value
            product.review_reason = f"{decision.reason}. Кандидаты: {decision.describe_candidates()}"

    log_action(
        db,
        component=COMPONENT,
        action=f"fgis_disambiguation:{task.model_name or manufacturer.legal_name}",
        result="needs_review",
        level=LogLevel.WARNING,
        details=f"{decision.reason}. Кандидаты: {decision.describe_candidates()}",
    )
    db.commit()

    return TaskOutcome(
        message=f"{decision.reason}. Кандидаты: {decision.describe_candidates()}",
        needs_review=True,
        details=details,
    )
