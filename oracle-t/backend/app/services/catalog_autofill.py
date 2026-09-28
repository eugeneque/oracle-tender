"""Автозаполнение каталога продукции: сквозной опрос всех источников по производителю
(замечание заказчика 28.09.2026 — «не должно быть пустых производителей, без кодов СИ, без
устройств»; пользователь открывает каталог и видит уже заполненное, остаётся проверить).

До этой правки каталог наполнялся шагами, которые запускались по отдельности: автопоиск в
ФГИС и привязка — кнопками, обход сайта и обучение по Аршину — раз в неделю. Производитель
без сайта-профиля и без нажатой кнопки оставался пустым.

Опрос идёт **двумя проходами** через общую очередь справочника (один поток — производители
строго по очереди, чужие серверы не нагружаются параллельно):

1. **Быстрый** (`catalog_autofill`, без обращений к модели, минуты на производителя):
   коды СИ в Госреестре → обход сайта, если для него есть профиль → привязка → модели из
   реестра (исполнения из карточек Аршина, а у типа без исполнений — базовая модель по
   обозначению) → привязка. После него у каждого производителя есть приборы с кодами СИ, и
   в списке каталога ставится галочка.
2. **Дополнение характеристик** (`catalog_learning`, тот же проход обучения, что и раньше):
   «Описания типа», поиск и разбор руководств — обращения к модели, десятки минут на
   производителя. Идёт после быстрого прохода по всем: иначе последний в очереди получил бы
   свои коды СИ через несколько часов (так и было в первом живом прогоне 28.09.2026).

Шаги изолированы: сбой одного (ФГИС недоступен, у RouterAI кончился баланс) не отменяет
остальные, а попадает в итог задачи. Пройденные шаги сохраняются в задаче, и после
перезапуска сервера (выкладка, `--reload` при разработке) опрос продолжается с того шага,
на котором оборвался, а не с начала — иначе частые перезапуски не давали опросу закончиться.

В конце полного прохода один раз читаются списки ПО верхнего уровня: они общие для всех и
строят связь по уже обновлённому каталогу.

Ручные записи не трогаются: и автопоиск, и привязка уважают подтверждённое человеком
(раздел 5.3 ТЗ — ручной ввод в приоритете).
"""

from __future__ import annotations

import uuid
from typing import Callable

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason, CatalogQueueStatus
from app.models.manufacturer import Manufacturer, Product, SiType
from app.services import catalog_queue_service
from app.services.catalog_queue_service import TaskOutcome

ADAPTER_KEY = "catalog_autofill"

# Подписи шагов — их же видит интерфейс в подсказке к значку производителя.
STEP_LABELS = {
    "si_search": "Коды СИ в ФГИС",
    "site": "Сайт производителя",
    "link": "Привязка кодов СИ",
    "registry": "Модели из реестра",
    "relink": "Привязка исполнений",
}

# Ключ второго прохода — обучение справочника (`app/services/catalog_learning.py`).
ENRICH_KEY = "catalog_learning"
# Долгие проходы: их выполняет только поток очереди, подбор «зависших» по расписанию — нет.
LONG_RUNNING_KEYS = (ADAPTER_KEY, ENRICH_KEY)


def register() -> None:
    catalog_queue_service.register_handler(ADAPTER_KEY, handle_task)


def enqueue(
    db: Session, manufacturer: Manufacturer, *, actor_id: uuid.UUID | None = None, run_now: bool = True
) -> CatalogLookupTask | None:
    return catalog_queue_service.enqueue(
        db,
        adapter_key=ADAPTER_KEY,
        reason=CatalogQueueReason.MANUAL if actor_id else CatalogQueueReason.SCHEDULED_REVALIDATION,
        manufacturer_id=manufacturer.id,
        # Имя в поле цели — чтобы повторный опрос того же производителя не вставал в
        # очередь дублем, пока первый не закончился (см. `catalog_queue_service.enqueue`).
        model_name=manufacturer.brand_name or manufacturer.legal_name,
        actor_id=actor_id,
        run_now=run_now,
    )


def queue_order(db: Session) -> list[Manufacturer]:
    """Порядок опроса: сначала производители без единой модели — им опрос нужнее всего,
    затем свой, затем остальные в порядке списка каталога (по доле рынка)."""

    from app.services.product_catalog_service import list_manufacturers

    with_products = set(db.scalars(select(Product.manufacturer_id).distinct()))
    manufacturers = list_manufacturers(db)
    return sorted(manufacturers, key=lambda m: (m.id in with_products, not m.is_mirtek))


def enqueue_all(db: Session, *, actor_id: uuid.UUID | None = None) -> int:
    """Ставит оба прохода по всем производителям сразу: сначала все быстрые, за ними все
    дополнения. Очередь идёт по времени постановки, поэтому порядок сохраняется и после
    перезапуска сервера — ничего не нужно помнить в памяти процесса."""

    from app.services import catalog_learning

    # Запуск во время идущего опроса присоединяется к нему: кто в этом прогоне уже опрошен,
    # заново не ставится — иначе он снова «в очереди», хотя только что закончил (так было
    # 28.09.2026 с МИРТЕК и Нартисом).
    run_started = current_run_started_at(db)
    finished_in_run = (
        set(
            db.scalars(
                select(CatalogLookupTask.manufacturer_id).where(
                    CatalogLookupTask.adapter_key == ADAPTER_KEY,
                    CatalogLookupTask.finished_at >= run_started,
                )
            )
        )
        if run_started is not None
        else set()
    )
    manufacturers = [m for m in queue_order(db) if m.id not in finished_in_run]
    queued = 0
    for manufacturer in manufacturers:
        if enqueue(db, manufacturer, actor_id=actor_id, run_now=False) is not None:
            queued += 1
    for manufacturer in manufacturers:
        catalog_learning.enqueue(db, manufacturer, actor_id=actor_id, run_now=False)
    return queued


def current_run_started_at(db: Session):
    """Начало идущего прогона — постановка самой ранней ещё не выполненной задачи; `None`,
    если опрос сейчас не идёт. Им же интерфейс считает «опрошено N из M»."""

    return db.scalar(
        select(func.min(CatalogLookupTask.created_at)).where(
            CatalogLookupTask.adapter_key == ADAPTER_KEY,
            CatalogLookupTask.status.in_([CatalogQueueStatus.QUEUED.value, CatalogQueueStatus.RUNNING.value]),
        )
    )


def run_all_in_background(*, actor_id: uuid.UUID | None = None) -> int:
    """Опрос всего каталога: постановка в очередь сразу, выполнение — в потоке очереди."""

    db = SessionLocal()
    try:
        queued = enqueue_all(db, actor_id=actor_id)
    finally:
        db.close()
    catalog_queue_service.submit(_process_all)
    return queued


def enqueue_one(db: Session, manufacturer: Manufacturer, *, actor_id: uuid.UUID | None = None) -> CatalogLookupTask | None:
    """Опрос одного производителя по кнопке: оба прохода, без чтения списков ПО в конце —
    они общие для всех и обновляются суточным проходом."""

    from app.services import catalog_learning

    task = enqueue(db, manufacturer, actor_id=actor_id, run_now=False)
    if task is None:
        return None
    catalog_learning.enqueue(db, manufacturer, actor_id=actor_id, run_now=False)
    catalog_queue_service.submit(lambda: _process_all(with_upper_software=False))
    return task


def resume_pending() -> None:
    """При старте: опрос, оборванный перезапуском, продолжается сам. Подбор «зависших»
    задач по расписанию долгие проходы не берёт (см. `_run_catalog_queue_job`), поэтому
    без этого они ждали бы следующего суточного запуска."""

    db = SessionLocal()
    try:
        pending = any(
            catalog_queue_service.pending_tasks(db, adapter_key=key, limit=1) for key in LONG_RUNNING_KEYS
        )
    finally:
        db.close()
    if pending:
        catalog_queue_service.submit(lambda: _process_all(with_upper_software=False))


def _process_all(*, with_upper_software: bool = True) -> None:
    # Предел с запасом: производителей десятки, а задачи уже стоят в очереди.
    for key, label in ((ADAPTER_KEY, "быстрый проход"), (ENRICH_KEY, "дополнение характеристик")):
        db = SessionLocal()
        try:
            counters = catalog_queue_service.process_queue(db, adapter_key=key, limit=500)
            logger.info(f"Автозаполнение каталога, {label}: {counters}")
        except Exception as exc:  # noqa: BLE001 - в потоке пула исключение никуда не долетит
            logger.exception(f"Автозаполнение каталога ({label}) сорвалось: {exc}")
        finally:
            db.close()
    if not with_upper_software:
        return

    from app.services import upper_software_service

    db = SessionLocal()
    try:
        for outcome in upper_software_service.sync_all(db):
            logger.info(f"ПО верхнего уровня {outcome.adapter_key}: {outcome.summary()}")
    except Exception as exc:  # noqa: BLE001 - списки ПО не должны ронять поток очереди
        logger.warning(f"Чтение списков ПО верхнего уровня после автозаполнения: {exc}")
    finally:
        db.close()


def handle_task(db: Session, task: CatalogLookupTask) -> TaskOutcome:
    manufacturer = db.get(Manufacturer, task.manufacturer_id) if task.manufacturer_id else None
    if manufacturer is None:
        return TaskOutcome(message="Производитель не указан либо удалён — опрашивать нечего")

    from app.services.catalog_learning import _actor

    actor = _actor(db)
    manufacturer_id = manufacturer.id
    # Шаги, пройденные до перезапуска сервера, не повторяются (задача вернулась в очередь
    # с сохранённым ходом, см. `catalog_queue_service.recover_interrupted_tasks`).
    steps: list[dict] = [s for s in (task.details or {}).get("steps") or [] if s.get("step") in STEP_LABELS]
    done = {s["step"] for s in steps} | set((task.details or {}).get("skipped_steps") or [])
    skipped: list[str] = sorted(done - {s["step"] for s in steps})

    def run_step(key: str, action: Callable[[Manufacturer], str | None]) -> None:
        if key in done:
            return
        # Текущий шаг — в задачу сразу, до выполнения: интерфейс показывает, на чём опрос.
        _save_progress(db, task, current_step=key, steps=steps, skipped=skipped)
        current = db.get(Manufacturer, manufacturer_id)
        try:
            message = action(current)
        except Exception as exc:  # noqa: BLE001 - сбой шага не отменяет остальные
            db.rollback()
            logger.warning(
                f"Автозаполнение «{current.legal_name}», шаг «{STEP_LABELS[key]}»: {exc}"
            )
            steps.append({"step": key, "ok": False, "message": str(exc)[:500]})
        else:
            if message is None:
                skipped.append(key)  # шаг неприменим (нет профиля сайта) — в итог не пишем
            else:
                steps.append({"step": key, "ok": True, "message": message})
        _save_progress(db, task, current_step=None, steps=steps, skipped=skipped)

    run_step("si_search", lambda m: _search_si_types(db, m, actor))
    run_step("site", lambda m: _sync_site(db, m, actor))
    run_step("link", lambda m: _link(db, m, actor))
    run_step("registry", lambda m: _registry(db, m, actor))
    run_step("relink", lambda m: _link(db, m, actor))

    failed = [s for s in steps if not s["ok"]]
    name = manufacturer.brand_name or manufacturer.legal_name
    counts = catalog_counts(db, manufacturer_id)
    summary = "; ".join(f"{STEP_LABELS[s['step']]}: {s['message']}" for s in steps)
    # Пустой каталог после опроса — не «готово», даже если все источники ответили: так
    # производитель без моделей выглядел заполненным (жалоба 28.09.2026).
    empty = counts["products"] == 0
    if empty:
        summary = f"моделей в каталоге нет; {summary}"
    return TaskOutcome(
        message=f"Опрос «{name}»: {summary}",
        # Сбой части шагов — не повод повторять весь проход (он занимает минуты), но
        # человек должен увидеть, что данные неполные.
        needs_review=bool(failed) or empty,
        details={
            "current_step": None,
            "steps": steps,
            "skipped_steps": skipped,
            "failed_steps": [s["step"] for s in failed],
            "empty": empty,
            **counts,
        },
    )


def catalog_counts(db: Session, manufacturer_id: uuid.UUID) -> dict[str, int]:
    return {
        "products": db.scalar(select(func.count()).select_from(Product).where(Product.manufacturer_id == manufacturer_id)) or 0,
        "si_types": db.scalar(select(func.count()).select_from(SiType).where(SiType.manufacturer_id == manufacturer_id)) or 0,
    }


def _save_progress(
    db: Session,
    task: CatalogLookupTask,
    *,
    current_step: str | None,
    steps: list[dict],
    skipped: list[str],
) -> None:
    task.details = {"current_step": current_step, "steps": list(steps), "skipped_steps": list(skipped)}
    db.commit()


def _search_si_types(db: Session, manufacturer: Manufacturer, actor) -> str:
    from app.adapters.fgis import FgisAdapter
    from app.services import product_catalog_service

    adapter = FgisAdapter()
    found = product_catalog_service.search_si_types(db, manufacturer, actor=actor, adapter=adapter)
    db.commit()
    if not found and adapter.errors:
        # «Найдено 0» при недоступном реестре — это сбой, а не пустой реестр.
        raise RuntimeError(f"ФГИС не ответил: {adapter.errors[0]}")
    return f"найдено {len(found)}"


def _site_adapter_for(db: Session, manufacturer: Manufacturer):
    from app.adapters.manufacturer_catalog import PROFILES
    from app.services import catalog_site_sync

    for key in (catalog_site_sync.MIRTEK_ADAPTER_KEY, *PROFILES):
        site = catalog_site_sync.resolve_site(db, key)
        if site is not None and site[0].id == manufacturer.id:
            return site[1]
    return None


def _sync_site(db: Session, manufacturer: Manufacturer, actor) -> str | None:
    from app.services import catalog_site_sync

    adapter = _site_adapter_for(db, manufacturer)
    if adapter is None:
        return None
    outcome = catalog_site_sync.sync_catalog(
        db, manufacturer=manufacturer, adapter=adapter, actor=actor
    )
    return (
        f"создано {outcome.products_created}, обновлено {outcome.products_updated}, "
        f"характеристик {outcome.characteristics_saved}"
    )


def _link(db: Session, manufacturer: Manufacturer, actor) -> str:
    from app.services import si_type_linking

    outcome = si_type_linking.link_products_to_si_types(db, manufacturer, actor_id=actor.id)
    db.commit()
    return f"привязано {outcome.linked}, без кода {outcome.not_found}"


def _registry(db: Session, manufacturer: Manufacturer, actor) -> str:
    """Модели из реестра без обращений к модели: карточки Аршина перечитываются ради
    списка исполнений, из него — записи каталога; тип без исполнений даёт базовую модель."""

    from app.services import catalog_learning, registry_modifications

    refreshed = catalog_learning.refresh_cards(db, manufacturer)
    outcome = registry_modifications.discover_modifications(db, manufacturer, actor_id=actor.id)
    db.commit()
    return (
        f"карточек Аршина обновлено {refreshed}; заведено моделей {outcome.products_created} "
        f"(из них по обозначению типа {outcome.base_models_created}), привязано {outcome.products_linked}"
    )


def status_by_manufacturer(db: Session) -> list[dict]:
    """Последний опрос каждого производителя — для значков в списке каталога.

    Берётся самая свежая задача: стоящая в очереди или идущая важнее прошлого итога."""

    # Последняя задача по каждому производителю — DISTINCT ON, а не «последние N задач»:
    # история опросов растёт каждые сутки, и предел по числу строк рано или поздно
    # отрезал бы производителей с давним опросом.
    rows = {
        task.manufacturer_id: task
        for task in db.scalars(
            select(CatalogLookupTask)
            .where(
                CatalogLookupTask.adapter_key == ADAPTER_KEY,
                CatalogLookupTask.manufacturer_id.is_not(None),
            )
            .distinct(CatalogLookupTask.manufacturer_id)
            .order_by(
                CatalogLookupTask.manufacturer_id,
                CatalogLookupTask.created_at.desc(),
                CatalogLookupTask.id.desc(),
            )
        )
    }

    # Дополнение характеристик идёт вторым проходом — в подсказке видно, что оно ещё впереди.
    enriching = set(
        db.scalars(
            select(CatalogLookupTask.manufacturer_id).where(
                CatalogLookupTask.adapter_key == ENRICH_KEY,
                CatalogLookupTask.status.in_(
                    [CatalogQueueStatus.QUEUED.value, CatalogQueueStatus.RUNNING.value]
                ),
            )
        )
    )

    return [
        {
            "manufacturer_id": manufacturer_id,
            "enriching": manufacturer_id in enriching,
            "status": task.status,
            "current_step": (task.details or {}).get("current_step"),
            "failed_steps": (task.details or {}).get("failed_steps") or [],
            "empty": bool((task.details or {}).get("empty")),
            "message": task.message,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }
        for manufacturer_id, task in rows.items()
    ]
