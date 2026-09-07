"""Очередь пополнения справочника продукции: постановка задач и их выполнение
(раздел 5.1, 5.3, 5.9 ТЗ; п.1.1 задания — два триггера через один адаптер).

Модуль отвечает только за **механику очереди** — поставить, взять, отметить исход,
повторить при сетевой ошибке, залогировать. Что именно делать с задачей, знают
исполнители, которые регистрируются здесь по ключу адаптера
(`app/services/fgis_catalog_sync.py`, `app/services/catalog_site_sync.py`).

Разделение не формальное: благодаря ему оба триггера ФГИС — суточная ревалидация и запрос
по событию из модуля сопоставления — идут по одному и тому же коду обращения к реестру,
как и требует п.1.1 задания. Триггер задаёт только `reason`, а не свой путь выполнения.

Регистрация исполнителей повторяет приём из `app/core/jobs.py`: очередь не импортирует
прикладные сервисы (иначе получился бы цикл импортов сервис → очередь → сервис), а они
регистрируются сами при импорте.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason, CatalogQueueStatus
from app.models.log import LogLevel
from app.services.audit import log_action

COMPONENT = "catalog_sync"

# Столько же попыток, сколько у фоновых задач анализа: сбои здесь той же природы (сеть,
# «источник занят»), и повод для повтора тот же (раздел 5.9 ТЗ).
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 5.0

# Сколько задач берётся за один прогон обработчика. Предел нужен, потому что ревалидация
# ставит в очередь все сохранённые типы СИ разом, а каждый запрос к ФГИС — это до 45 секунд
# (см. `SEARCH_TIMEOUT_SECONDS` в адаптере): без предела один прогон занял бы часы и
# перекрылся бы со следующим по расписанию.
DEFAULT_BATCH_SIZE = 25

# Один поток: и ФГИС, и сайт производителя — чужие серверы, к которым мы ходим без
# договорённости. Параллелить обращения к ним ради скорости заполнения своего справочника
# было бы невежливо, а выигрыш всё равно съел бы таймаут ФГИС.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oraclet-catalog")


@dataclass
class TaskOutcome:
    """Чем закончилась обработка одной задачи.

    `needs_review` отделён от `success` и от `error` намеренно: обращение к источнику
    прошло штатно, но результат нельзя принять автоматически (несколько кандидатов в
    реестре либо кандидат не того вида измерений). Повторять такую задачу бессмысленно —
    её должен посмотреть человек."""

    message: str
    needs_review: bool = False
    details: dict | None = None


# Ключ адаптера → исполнитель. Исполнитель получает открытую сессию и задачу, возвращает
# исход; исключение из него означает «повторить» (см. `run_task`).
_HANDLERS: dict[str, Callable[[Session, CatalogLookupTask], TaskOutcome]] = {}


def register_handler(
    adapter_key: str, handler: Callable[[Session, CatalogLookupTask], TaskOutcome]
) -> None:
    _HANDLERS[adapter_key] = handler


def registered_handlers() -> list[str]:
    return sorted(_HANDLERS)


def enqueue(
    db: Session,
    *,
    adapter_key: str,
    reason: CatalogQueueReason,
    model_name: str | None = None,
    manufacturer_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    si_type_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    run_now: bool = True,
) -> CatalogLookupTask | None:
    """Ставит задачу в очередь и (по умолчанию) сразу запускает обработку в фоне.

    `run_now=True` — это и есть требование п.1.1 задания «не ждать следующего цикла»:
    когда модуль сопоставления натыкается на модель без данных, запрос уходит в реестр
    немедленно, а не через сутки. Плановый прогон при этом никуда не девается — он
    подбирает то, что не успело выполниться или сорвалось.

    Дубликат не создаётся: та же цель, уже стоящая в очереди или выполняющаяся, возвращается
    как есть. Иначе один тендер с полусотней требований поставил бы полсотни одинаковых
    запросов к ФГИС по одной и той же модели.
    """

    if adapter_key not in _HANDLERS:
        logger.warning(
            f"Очередь справочника: исполнитель для источника «{adapter_key}» не зарегистрирован — "
            "задача не поставлена"
        )
        return None

    # Сравнение через `is_not_distinct_from`, а не `==`: цель задачи задаётся частью полей,
    # остальные — NULL, а `NULL = NULL` в SQL даёт NULL, и дубликат по «пустой» части ключа
    # никогда бы не нашёлся.
    query = select(CatalogLookupTask).where(
        CatalogLookupTask.adapter_key == adapter_key,
        CatalogLookupTask.status.in_(
            [CatalogQueueStatus.QUEUED.value, CatalogQueueStatus.RUNNING.value]
        ),
    )
    for column, value in (
        (CatalogLookupTask.model_name, model_name),
        (CatalogLookupTask.product_id, product_id),
        (CatalogLookupTask.si_type_id, si_type_id),
    ):
        query = query.where(column.is_not_distinct_from(value))

    existing = db.scalar(query.order_by(CatalogLookupTask.created_at.desc()).limit(1))
    if existing is not None:
        return existing

    task = CatalogLookupTask(
        adapter_key=adapter_key,
        reason=reason.value,
        status=CatalogQueueStatus.QUEUED.value,
        manufacturer_id=manufacturer_id,
        product_id=product_id,
        si_type_id=si_type_id,
        model_name=model_name,
        details={},
    )
    db.add(task)
    log_action(
        db,
        component=COMPONENT,
        action=f"enqueue:{adapter_key}",
        result="queued",
        level=LogLevel.INFO,
        details=f"{reason.value}: {model_name or si_type_id or manufacturer_id}",
        user_id=actor_id,
    )
    db.commit()
    db.refresh(task)

    if run_now:
        _executor.submit(process_queue_in_background)
    return task


def pending_tasks(db: Session, *, adapter_key: str | None = None, limit: int = DEFAULT_BATCH_SIZE) -> list[CatalogLookupTask]:
    query = select(CatalogLookupTask).where(
        CatalogLookupTask.status == CatalogQueueStatus.QUEUED.value
    )
    if adapter_key is not None:
        query = query.where(CatalogLookupTask.adapter_key == adapter_key)
    return list(db.scalars(query.order_by(CatalogLookupTask.created_at).limit(limit)))


def list_tasks(
    db: Session,
    *,
    adapter_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[CatalogLookupTask]:
    """Задачи очереди, свежие сверху. Основной сценарий использования — фильтр
    `status="needs_review"`: рабочий список того, что система отказалась решать сама."""

    query = select(CatalogLookupTask)
    if adapter_key is not None:
        query = query.where(CatalogLookupTask.adapter_key == adapter_key)
    if status is not None:
        query = query.where(CatalogLookupTask.status == status)
    return list(db.scalars(query.order_by(CatalogLookupTask.created_at.desc()).limit(limit)))


def process_queue(
    db: Session, *, adapter_key: str | None = None, limit: int = DEFAULT_BATCH_SIZE
) -> dict[str, int]:
    """Обрабатывает пачку задач. Возвращает счётчики по исходам.

    Ошибка одной задачи не прерывает остальные (раздел 5.9 ТЗ) — ровно тот же принцип
    изоляции, что и у адаптеров тендерных площадок: одна битая карточка не должна оставить
    справочник без всех остальных.
    """

    counters = {"success": 0, "needs_review": 0, "error": 0}
    for task in pending_tasks(db, adapter_key=adapter_key, limit=limit):
        status = run_task(db, task)
        if status is CatalogQueueStatus.SUCCESS:
            counters["success"] += 1
        elif status is CatalogQueueStatus.NEEDS_REVIEW:
            counters["needs_review"] += 1
        else:
            counters["error"] += 1
    return counters


def process_queue_in_background() -> None:
    """Тело фоновой обработки: своя сессия БД, все ошибки гасятся здесь же.

    Отдельная сессия обязательна — задача выполняется в другом потоке, а сессия HTTP-запроса
    к этому моменту уже закрыта (та же причина, что и в `app/core/jobs.py`)."""

    db = SessionLocal()
    try:
        counters = process_queue(db)
        if any(counters.values()):
            logger.info(f"Очередь справочника продукции обработана: {counters}")
    except Exception as exc:  # noqa: BLE001 - необработанное исключение в потоке пула не долетит никуда
        logger.exception(f"Обработка очереди справочника продукции сорвалась: {exc}")
    finally:
        db.close()


def run_task(db: Session, task: CatalogLookupTask) -> CatalogQueueStatus:
    """Выполняет одну задачу с повторными попытками при сетевых сбоях."""

    handler = _HANDLERS.get(task.adapter_key)
    if handler is None:
        return _finish(
            db,
            task,
            CatalogQueueStatus.ERROR,
            f"Исполнитель для источника «{task.adapter_key}» не зарегистрирован",
        )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        task.status = CatalogQueueStatus.RUNNING.value
        task.started_at = task.started_at or datetime.now(timezone.utc)
        task.attempts = attempt
        db.commit()

        try:
            outcome = handler(db, task)
        except Exception as exc:  # noqa: BLE001 - сбой задачи не должен ронять остальную очередь
            db.rollback()
            logger.warning(
                f"Очередь справочника: задача {task.adapter_key}/{task.model_name} "
                f"(попытка {attempt} из {MAX_ATTEMPTS}) завершилась ошибкой: {exc}"
            )
            if attempt >= MAX_ATTEMPTS:
                return _finish(db, task, CatalogQueueStatus.ERROR, str(exc))
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        status = (
            CatalogQueueStatus.NEEDS_REVIEW if outcome.needs_review else CatalogQueueStatus.SUCCESS
        )
        return _finish(db, task, status, outcome.message, details=outcome.details)

    return CatalogQueueStatus.ERROR  # недостижимо: цикл выходит только через `_finish`


def _finish(
    db: Session,
    task: CatalogLookupTask,
    status: CatalogQueueStatus,
    message: str | None,
    *,
    details: dict | None = None,
) -> CatalogQueueStatus:
    task.status = status.value
    task.finished_at = datetime.now(timezone.utc)
    task.message = message
    if details is not None:
        task.details = details

    log_action(
        db,
        component=COMPONENT,
        action=f"lookup:{task.adapter_key}",
        result=status.value,
        level={
            CatalogQueueStatus.SUCCESS: LogLevel.INFO,
            # «Требует проверки» — WARNING, а не INFO: это прямое требование п.1.2 задания.
            # Такая запись должна попадаться на глаза при просмотре журнала, иначе неверное
            # совпадение так и останется незамеченным в справочнике.
            CatalogQueueStatus.NEEDS_REVIEW: LogLevel.WARNING,
            CatalogQueueStatus.ERROR: LogLevel.ERROR,
        }.get(status, LogLevel.INFO),
        details=f"{task.model_name or task.si_type_id or '—'}: {message or ''}",
    )
    db.commit()

    if status is CatalogQueueStatus.ERROR:
        # Сорвавшийся после всех попыток запрос — то самое «критическое», о чём раздел 5.9 ТЗ
        # требует уведомить администратора.
        from app.services import notification_service

        notification_service.notify_critical_error(
            db,
            subject=f"Пополнение справочника из источника «{task.adapter_key}» завершилось ошибкой",
            details=f"{task.model_name or task.si_type_id or ''}\n{message or ''}",
        )
    return status


def recover_interrupted_tasks() -> None:
    """При старте приложения помечает задачи, оборванные предыдущим запуском.

    Пул живёт в памяти процесса: после перезапуска «выполняется» в базе — это задача,
    которую уже никто не считает. Она возвращается в очередь, а не помечается ошибкой (в
    отличие от `BackgroundJob`): пользователь её не запускал и не ждёт, а данные справочника
    нужны системе в любом случае."""

    db = SessionLocal()
    try:
        stale = list(
            db.scalars(
                select(CatalogLookupTask).where(
                    CatalogLookupTask.status == CatalogQueueStatus.RUNNING.value
                )
            )
        )
        for task in stale:
            task.status = CatalogQueueStatus.QUEUED.value
            task.started_at = None
        if stale:
            log_action(
                db,
                component=COMPONENT,
                action="recover_interrupted",
                result="success",
                level=LogLevel.WARNING,
                details=f"Возвращено в очередь задач: {len(stale)}",
            )
            db.commit()
            logger.warning(f"Возвращено в очередь справочника задач после перезапуска: {len(stale)}")
    finally:
        db.close()


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
