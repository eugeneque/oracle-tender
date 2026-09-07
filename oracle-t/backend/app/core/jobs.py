"""Фоновое выполнение долгих операций — ИИ-анализ тендера и расчёт соответствия
(разделы 5.4, 5.5 ТЗ), плюс автоматические повторные попытки при сбое (раздел 5.9 ТЗ).

Почему свой мини-пул, а не Celery/RQ: очередь обслуживает ровно две операции, обе внутри
одного процесса приложения, и обе уже умеют изолировать свои ошибки. Отдельный брокер
(Redis + воркер) добавил бы к развёртыванию ещё два сервиса ради двух кнопок в интерфейсе —
раздел 6.3 ТЗ прямо просит не усложнять инфраструктуру без необходимости.

Пул — один на процесс, две параллельные задачи: YandexGPT всё равно узкое место, а больше
двух одновременных разборов документации упираются в квоты модели и в память под тексты.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.user import User
from app.services.audit import log_action

MAX_ATTEMPTS = 2
# Пауза перед повторной попыткой: сбои YandexGPT почти всегда сетевые или «модель занята»,
# мгновенный повтор упёрся бы в ту же ошибку.
RETRY_DELAY_SECONDS = 5.0

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="oraclet-job")

# Обработчики регистрируются при импорте `app.services.job_runner` — так модуль очереди не
# зависит от прикладных сервисов (иначе получился бы цикл импортов: сервис → очередь → сервис).
_HANDLERS: dict[str, Callable[[Session, Tender, User | None], str]] = {}


def register_handler(kind: JobKind, handler: Callable[[Session, Tender, User | None], str]) -> None:
    _HANDLERS[kind.value] = handler


def enqueue(db: Session, *, kind: JobKind, tender: Tender, actor: User | None) -> BackgroundJob:
    """Ставит задачу в очередь и сразу возвращает её — HTTP-ответ не ждёт выполнения.

    Повторный запуск того же вида по тому же тендеру не создаёт вторую задачу: пользователь
    жмёт кнопку ещё раз, не дождавшись результата, а два параллельных анализа одного тендера
    затирали бы требования друг друга."""

    existing = db.execute(
        select(BackgroundJob)
        .where(
            BackgroundJob.tender_id == tender.id,
            BackgroundJob.kind == kind.value,
            BackgroundJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    job = BackgroundJob(
        kind=kind.value,
        status=JobStatus.QUEUED.value,
        tender_id=tender.id,
        created_by_id=actor.id if actor else None,
    )
    db.add(job)
    log_action(
        db,
        component="jobs",
        action=f"enqueue:{kind.value}",
        result="queued",
        level=LogLevel.INFO,
        details=f"Тендер {tender.external_id}",
        user_id=actor.id if actor else None,
    )
    db.commit()
    db.refresh(job)

    _executor.submit(run_job, job.id)
    return job


def run_job(job_id: uuid.UUID) -> None:
    """Тело фоновой задачи. Выполняется в отдельном потоке со своей сессией БД: сессия
    HTTP-запроса к этому моменту уже закрыта, а делить одну Session между потоками нельзя.

    Любая ошибка гасится здесь же и попадает в журнал: необработанное исключение в потоке
    пула не долетит ни до пользователя, ни до лога (раздел 5.9 ТЗ — изоляция ошибок)."""

    db = SessionLocal()
    try:
        job = db.get(BackgroundJob, job_id)
        if job is None:
            logger.warning(f"Фоновая задача {job_id} не найдена")
            return

        handler = _HANDLERS.get(job.kind)
        tender = db.get(Tender, job.tender_id) if job.tender_id else None
        if handler is None or tender is None:
            _finish(db, job, JobStatus.ERROR, "Задача не может быть выполнена: неизвестный вид или тендер удалён")
            return

        actor = db.get(User, job.created_by_id) if job.created_by_id else None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            job.status = JobStatus.RUNNING.value
            job.started_at = job.started_at or datetime.now(timezone.utc)
            job.attempts = attempt
            db.commit()

            try:
                message = handler(db, tender, actor)
            except Exception as exc:  # noqa: BLE001 - сбой задачи не должен ронять поток пула
                db.rollback()
                logger.warning(
                    f"Фоновая задача {job.kind} по тендеру {tender.external_id} "
                    f"(попытка {attempt} из {MAX_ATTEMPTS}) завершилась ошибкой: {exc}"
                )
                if attempt >= MAX_ATTEMPTS:
                    _finish(db, job, JobStatus.ERROR, str(exc), tender=tender, actor=actor)
                    return
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            _finish(db, job, JobStatus.SUCCESS, message, tender=tender, actor=actor)
            return
    finally:
        db.close()


def _finish(
    db: Session,
    job: BackgroundJob,
    status: JobStatus,
    message: str | None,
    *,
    tender: Tender | None = None,
    actor: User | None = None,
) -> None:
    job.status = status.value
    job.finished_at = datetime.now(timezone.utc)
    job.message = message
    log_action(
        db,
        component="jobs",
        action=f"finish:{job.kind}",
        result=status.value,
        level=LogLevel.INFO if status is JobStatus.SUCCESS else LogLevel.ERROR,
        details=(f"Тендер {tender.external_id}: " if tender else "") + (message or ""),
        user_id=actor.id if actor else None,
    )
    db.commit()

    if status is JobStatus.ERROR:
        # Сорвавшийся после всех попыток анализ — то самое «критическое», о чём раздел 5.9 ТЗ
        # требует уведомить администратора.
        from app.services import notification_service

        notification_service.notify_critical_error(
            db,
            subject=f"Фоновая задача {job.kind} завершилась ошибкой",
            details=(f"Тендер {tender.external_id}\n" if tender else "") + (message or ""),
        )


def recover_interrupted_jobs() -> None:
    """При старте приложения помечает задачи, оборванные предыдущим запуском.

    Пул живёт в памяти процесса: после перезапуска сервера «выполняется» в базе — это
    задача, которую уже никто не считает. Оставить её висеть значило бы вечный «идёт
    анализ…» в карточке тендера."""

    db = SessionLocal()
    try:
        stale = (
            db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])
                )
            )
            .scalars()
            .all()
        )
        for job in stale:
            job.status = JobStatus.ERROR.value
            job.finished_at = datetime.now(timezone.utc)
            job.message = "Прервана перезапуском сервера — запустите заново"
        if stale:
            log_action(
                db,
                component="jobs",
                action="recover_interrupted",
                result="success",
                level=LogLevel.WARNING,
                details=f"Помечено прерванными задач: {len(stale)}",
            )
            db.commit()
            logger.warning(f"Помечено прерванными фоновых задач: {len(stale)}")
    finally:
        db.close()


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
