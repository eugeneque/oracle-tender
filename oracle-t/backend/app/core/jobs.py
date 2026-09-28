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
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable

from loguru import logger
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.user import User
from app.services.ai_context import acting_as
from app.services.audit import log_action

MAX_ATTEMPTS = 2
# Пауза перед повторной попыткой: сбои YandexGPT почти всегда сетевые или «модель занята»,
# мгновенный повтор упёрся бы в ту же ошибку.
RETRY_DELAY_SECONDS = 5.0

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="oraclet-job")
# Опрос площадок — в своём пуле на один поток (17.09.2026): он идёт 20–25 минут и занял бы
# половину общего пула, оставив ИИ-анализу один поток; а два одновременных опроса одних и
# тех же площадок бессмысленны — второй запрос и так возвращает уже идущую задачу.
_poll_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oraclet-poll")


def _submit_poll(job_id: uuid.UUID) -> None:
    """Пул опроса пересоздаётся после `shutdown()`: в тестах приложение поднимается и гасится
    на каждый `TestClient`, и закрытый пул отвечал бы «cannot schedule new futures»."""

    global _poll_executor
    if _poll_executor._shutdown:  # noqa: SLF001 - у ThreadPoolExecutor нет публичного признака
        _poll_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="oraclet-poll")
    _poll_executor.submit(run_job, job_id)

# Обработчики регистрируются при импорте `app.services.job_runner` — так модуль очереди не
# зависит от прикладных сервисов (иначе получился бы цикл импортов: сервис → очередь → сервис).
_HANDLERS: dict[str, Callable[[Session, Tender, User | None], str]] = {}
# Обработчики задач без тендера (опрос площадок): получают саму задачу — входные данные
# лежат в её `payload`, и туда же по ходу пишется прогресс.
_JOB_HANDLERS: dict[str, Callable[[Session, BackgroundJob, User | None], str]] = {}


# Задача, которую выполняет текущий поток: (id, момент постановки в очередь). Обработчики
# уровня тендера получают только тендер, а сообщить о ходе («матрица: 6 из 17
# производителей») и узнать, что уже посчитано до перезапуска сервера, им нужно (28.09.2026:
# карточка 10 минут показывала «обычно около минуты» без единого признака жизни).
_current_job: ContextVar[tuple[uuid.UUID, datetime] | None] = ContextVar(
    "oraclet_current_job", default=None
)


def current_job_created_at() -> datetime | None:
    """Когда текущая задача встала в очередь. Всё, что посчитано позже, посчитано ею же —
    до перезапуска сервера, — и пересчитывать это после продолжения незачем."""

    current = _current_job.get()
    return current[1] if current else None


def report_progress(message: str) -> None:
    """Пишет ход текущей задачи в `message` — карточка показывает его вместо безымянного
    индикатора. Отдельной сессией и отдельной транзакцией: у обработчика может быть открыта
    своя, и коммитить её ради строки прогресса нельзя. Вне задачи (HTTP-запрос, тест) ничего
    не делает; сбой записи работу не прерывает.

    Прогресс заодно обнуляет счётчик перезапусков: задача, продвинувшаяся после прошлого
    перезапуска, не «роняет сервер собой», и после третьего `--reload` за разбор её нельзя
    помечать ошибкой (см. `recover_interrupted_jobs`)."""

    current = _current_job.get()
    if current is None:
        return
    db = SessionLocal()
    try:
        # Строку задачи может держать транзакция обработчика — ждать её ради прогресса
        # нельзя, одно пропущенное обновление не страшно.
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
        db.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == current[0],
                BackgroundJob.status == JobStatus.RUNNING.value,
            )
            .values(
                message=message[:500],
                payload=BackgroundJob.payload.op("-")("restarts"),
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - прогресс не должен ронять задачу
        db.rollback()
        logger.debug(f"Прогресс задачи {current[0]} не записан: {exc}")
    finally:
        db.close()


def register_handler(kind: JobKind, handler: Callable[[Session, Tender, User | None], str]) -> None:
    _HANDLERS[kind.value] = handler


def register_job_handler(
    kind: JobKind, handler: Callable[[Session, BackgroundJob, User | None], str]
) -> None:
    _JOB_HANDLERS[kind.value] = handler


def enqueue_standalone(
    db: Session, *, kind: JobKind, payload: dict, actor: User | None
) -> BackgroundJob:
    """Ставит в очередь задачу без тендера (опрос площадок). Пока такая задача стоит в
    очереди или идёт, повторный вызов возвращает её же: второй опрос тех же площадок поверх
    первого только удвоил бы нагрузку на сайты и время ожидания."""

    existing = db.execute(
        select(BackgroundJob)
        .where(
            BackgroundJob.tender_id.is_(None),
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
        payload=payload,
        created_by_id=actor.id if actor else None,
    )
    db.add(job)
    log_action(
        db,
        component="jobs",
        action=f"enqueue:{kind.value}",
        result="queued",
        level=LogLevel.INFO,
        user_id=actor.id if actor else None,
    )
    db.commit()
    db.refresh(job)

    _submit_poll(job.id)
    return job


def latest_standalone(db: Session, kind: JobKind) -> BackgroundJob | None:
    """Последняя задача этого вида без тендера — идущая или уже завершённая. Интерфейс
    спрашивает её при входе на страницу: опрос, запущенный до перезагрузки вкладки, должен
    быть виден, а не потерян."""

    return db.execute(
        select(BackgroundJob)
        .where(BackgroundJob.tender_id.is_(None), BackgroundJob.kind == kind.value)
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


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

        actor = db.get(User, job.created_by_id) if job.created_by_id else None
        # Поток пула переиспользуется: контекст выставляется на каждую задачу заново.
        _current_job.set((job.id, job.created_at))

        # Задача без тендера — одна попытка: опрос площадок сам изолирует ошибку каждой
        # площадки, а повторять 25-минутный проход целиком из-за сбоя было бы хуже, чем
        # честно показать ошибку.
        job_handler = _JOB_HANDLERS.get(job.kind)
        if job_handler is not None:
            job.status = JobStatus.RUNNING.value
            job.started_at = datetime.now(timezone.utc)
            job.attempts = 1
            db.commit()
            try:
                # Модель ИИ — та, что выбрал автор задачи (см. app/services/ai_context.py);
                # у задач без автора (расписание) — системная по умолчанию.
                with acting_as(actor.id if actor else None):
                    message = job_handler(db, job, actor)
            except Exception as exc:  # noqa: BLE001 - сбой задачи не должен ронять поток пула
                db.rollback()
                logger.exception(f"Фоновая задача {job.kind} завершилась ошибкой")
                _finish(db, job, JobStatus.ERROR, str(exc), actor=actor)
                return
            _finish(db, job, JobStatus.SUCCESS, message, actor=actor)
            return

        handler = _HANDLERS.get(job.kind)
        tender = db.get(Tender, job.tender_id) if job.tender_id else None
        if handler is None or tender is None:
            _finish(db, job, JobStatus.ERROR, "Задача не может быть выполнена: неизвестный вид или тендер удалён")
            return

        for attempt in range(1, MAX_ATTEMPTS + 1):
            job.status = JobStatus.RUNNING.value
            job.started_at = job.started_at or datetime.now(timezone.utc)
            job.attempts = attempt
            db.commit()

            try:
                with acting_as(actor.id if actor else None):
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
        _current_job.set(None)
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


# Сколько раз задача переживает перезапуск сервера. Больше — значит, она сама его и
# вызывает (падение процесса на конкретном тендере), и крутить её по кругу нельзя.
MAX_RESTARTS = 2


def recover_interrupted_jobs(*, resume: bool | None = None) -> None:
    """При старте приложения продолжает задачи, оборванные предыдущим запуском.

    Пул живёт в памяти процесса: после перезапуска сервера «выполняется» в базе — это
    задача, которую уже никто не считает. Раньше она помечалась ошибкой «запустите заново»,
    и разбор оставался с дырой (28.09.2026: «Полный разбор» оборвался на матрице, а
    «Обновить разбор» её не строил — заключение вышло без проверки по ТЗ). Теперь задача
    встаёт в очередь снова; «Полный разбор» продолжает с того шага, на котором оборвался
    (итоги пройденных шагов — в `payload`). Ошибкой задача помечается, только если пережила
    уже `MAX_RESTARTS` перезапусков (или продолжение выключено настройкой
    `jobs_resume_interrupted`)."""

    if resume is None:
        resume = get_settings().jobs_resume_interrupted
    db = SessionLocal()
    resumed: list[BackgroundJob] = []
    ids: list[tuple[uuid.UUID, str]] = []
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
        failed = 0
        for job in stale:
            restarts = int((job.payload or {}).get("restarts") or 0)
            known = job.kind in _HANDLERS or job.kind in _JOB_HANDLERS
            if not resume or not known or restarts >= MAX_RESTARTS:
                job.status = JobStatus.ERROR.value
                job.finished_at = datetime.now(timezone.utc)
                job.message = (
                    f"Прервана перезапуском сервера {restarts + 1} раз подряд — запустите заново"
                )
                failed += 1
                continue
            job.status = JobStatus.QUEUED.value
            job.started_at = None
            job.message = "Продолжается после перезапуска сервера…"
            job.payload = {**(job.payload or {}), "restarts": restarts + 1}
            resumed.append(job)
        if stale:
            log_action(
                db,
                component="jobs",
                action="recover_interrupted",
                result="success",
                level=LogLevel.WARNING,
                details=f"Продолжено задач: {len(resumed)}, помечено прерванными: {failed}",
            )
            db.commit()
            logger.warning(
                f"Оборванные фоновые задачи: продолжено {len(resumed)}, прервано {failed}"
            )
        ids = [(job.id, job.kind) for job in resumed]
    finally:
        db.close()

    for job_id, kind in ids:
        if kind in _JOB_HANDLERS and kind != JobKind.TENDER_FULL_REVIEW.value:
            _submit_poll(job_id)
        else:
            _executor.submit(run_job, job_id)


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
    _poll_executor.shutdown(wait=False, cancel_futures=True)
