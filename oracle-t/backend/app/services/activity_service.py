"""Центр уведомлений в шапке (14.10.2026): что запускал пользователь, чем это кончилось,
и какие общие процессы идут сейчас.

Пользователь запускал разбор закупки или опрос каталога, уходил на другую страницу и не
узнавал, что всё готово, — пока не возвращался в карточку. Здесь в один список сведены три
источника: `background_jobs` (разборы, опрос площадок по кнопке), очередь справочника
`catalog_lookup_queue` (опрос каталога, обход сайтов, сверка документов) и задания
планировщика (`app.core.activity`). Интерфейс опрашивает список и подаёт звуковой сигнал,
когда процесс пользователя закончился.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import activity
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueStatus
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.tender import Tender
from app.models.user import User
from app.schemas.activity import ActivityFeed, ActivityItem
from app.services.job_queue_service import KIND_LABELS

# Итоги старше суток не нужны: центр уведомлений — про текущую работу, история — в журнале.
RECENT_WINDOW = timedelta(hours=24)
MINE_LIMIT = 20
SYSTEM_RECENT_LIMIT = 5
# Задачи очереди справочника, поставленные одним нажатием («Опросить каталог» ставит по две
# на каждого производителя), идут в центре одной строкой с общей шкалой.
CATALOG_BATCH_GAP = timedelta(minutes=2)

_ACTIVE_JOB = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)
_ACTIVE_CATALOG = (CatalogQueueStatus.QUEUED.value, CatalogQueueStatus.RUNNING.value)

CATALOG_LABELS = {
    "catalog_autofill": "Опрос каталога",
    "catalog_learning": "Дополнение характеристик",
    "fgis": "Поиск в ФГИС «Аршин»",
    "document_registry": "Сверка документов по СИ",
}


def _catalog_label(adapter_key: str) -> str:
    if adapter_key in CATALOG_LABELS:
        return CATALOG_LABELS[adapter_key]
    from app.adapters.manufacturer_catalog import PROFILES
    from app.services.catalog_site_sync import MIRTEK_ADAPTER_KEY

    if adapter_key == MIRTEK_ADAPTER_KEY or adapter_key in PROFILES:
        return "Обход сайта производителя"
    return "Справочник продукции"


def _job_progress(job: BackgroundJob) -> float | None:
    if job.kind != JobKind.SOURCES_POLL.value:
        return None
    payload = job.payload or {}
    total = len(payload.get("source_keys") or [])
    done = len(payload.get("results") or [])
    if job.status not in _ACTIVE_JOB:
        return 1.0
    return min(done / total, 1.0) if total else None


def _job_items(db: Session, jobs: list[BackgroundJob], *, scope: str, names: dict) -> list[ActivityItem]:
    tender_ids = {job.tender_id for job in jobs if job.tender_id}
    tenders = (
        {
            row.id: row
            for row in db.execute(
                select(Tender.id, Tender.title, Tender.external_id).where(Tender.id.in_(tender_ids))
            ).all()
        }
        if tender_ids
        else {}
    )
    items = []
    for job in jobs:
        tender = tenders.get(job.tender_id) if job.tender_id else None
        label = KIND_LABELS.get(job.kind, job.kind)
        is_poll = job.kind == JobKind.SOURCES_POLL.value
        items.append(
            ActivityItem(
                id=f"job:{job.id}",
                scope=scope,  # type: ignore[arg-type]
                kind=job.kind,
                title=(tender.title if tender else None) or label,
                subtitle=(
                    f"{label} · № {tender.external_id}" if tender and tender.external_id else label
                ),
                status=job.status,  # type: ignore[arg-type]
                progress=_job_progress(job),
                message=job.message,
                started_at=job.started_at or job.created_at,
                finished_at=job.finished_at,
                link=f"/tenders/{job.tender_id}" if job.tender_id else ("/tenders" if is_poll else None),
                job_id=job.id if not is_poll else None,
                owner_name=names.get(job.created_by_id) if scope == "system" else None,
            )
        )
    return items


def _catalog_status(tasks: list[CatalogLookupTask]) -> str:
    statuses = {task.status for task in tasks}
    if CatalogQueueStatus.RUNNING.value in statuses:
        return "running"
    if CatalogQueueStatus.QUEUED.value in statuses:
        # Часть уже сделана — для человека это «идёт», а не «ждёт».
        return "running" if len(statuses) > 1 else "queued"
    return "error" if CatalogQueueStatus.ERROR.value in statuses else "success"


def _catalog_message(tasks: list[CatalogLookupTask]) -> str | None:
    """Что делается сейчас (идёт) или итог (закончено)."""

    from app.services.catalog_autofill import STEP_LABELS

    running = next((t for t in tasks if t.status == CatalogQueueStatus.RUNNING.value), None)
    if running is not None:
        step = STEP_LABELS.get((running.details or {}).get("current_step") or "")
        parts = [running.model_name, step]
        return " · ".join(p for p in parts if p) or _catalog_label(running.adapter_key)
    if any(t.status in _ACTIVE_CATALOG for t in tasks):
        return None
    errors = sum(1 for t in tasks if t.status == CatalogQueueStatus.ERROR.value)
    review = sum(1 for t in tasks if t.status == CatalogQueueStatus.NEEDS_REVIEW.value)
    if len(tasks) == 1:
        return tasks[0].message
    parts = [f"выполнено {len(tasks) - errors}"]
    if errors:
        parts.append(f"с ошибкой {errors}")
    if review:
        parts.append(f"на проверку {review}")
    return "Задач " + str(len(tasks)) + ": " + ", ".join(parts)


def _catalog_item(tasks: list[CatalogLookupTask], *, scope: str, item_id: str) -> ActivityItem:
    done = sum(1 for t in tasks if t.status not in _ACTIVE_CATALOG)
    status = _catalog_status(tasks)
    keys = {t.adapter_key for t in tasks}
    if len(tasks) == 1:
        task = tasks[0]
        title = _catalog_label(task.adapter_key)
        subtitle = task.model_name
    else:
        title = (
            "Опрос каталога"
            if keys <= {"catalog_autofill", "catalog_learning"}
            else _catalog_label(sorted(keys)[0]) if len(keys) == 1 else "Справочник продукции"
        )
        targets = {t.manufacturer_id or t.model_name for t in tasks}
        subtitle = f"Производителей: {len(targets)}" if len(targets) > 1 else tasks[0].model_name
    finished = [t.finished_at for t in tasks if t.finished_at]
    return ActivityItem(
        id=item_id,
        scope=scope,  # type: ignore[arg-type]
        kind="catalog",
        title=title,
        subtitle=subtitle,
        status=status,  # type: ignore[arg-type]
        progress=done / len(tasks) if len(tasks) > 1 or status not in ("queued", "running") else None,
        message=_catalog_message(tasks),
        started_at=min(t.started_at or t.created_at for t in tasks),
        finished_at=max(finished) if finished and status not in ("queued", "running") else None,
        link="/catalog",
    )


def _batches(tasks: list[CatalogLookupTask]) -> list[list[CatalogLookupTask]]:
    """Задачи одного нажатия: поставлены подряд, с промежутком не больше пары минут."""

    batches: list[list[CatalogLookupTask]] = []
    for task in sorted(tasks, key=lambda t: t.created_at):
        if batches and task.created_at - batches[-1][-1].created_at <= CATALOG_BATCH_GAP:
            batches[-1].append(task)
        else:
            batches.append([task])
    return batches


def _mine(db: Session, user: User, since: datetime) -> list[ActivityItem]:
    recent_or_active = lambda model: or_(  # noqa: E731 - одно условие на две таблицы
        model.status.in_(_ACTIVE_JOB), model.finished_at >= since, model.created_at >= since
    )
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.created_by_id == user.id, recent_or_active(BackgroundJob))
            .order_by(BackgroundJob.created_at.desc())
            .limit(MINE_LIMIT)
        )
    )
    items = _job_items(db, jobs, scope="mine", names={})

    tasks = list(
        db.scalars(
            select(CatalogLookupTask)
            .where(CatalogLookupTask.created_by_id == user.id, recent_or_active(CatalogLookupTask))
            .order_by(CatalogLookupTask.created_at.desc())
            .limit(500)
        )
    )
    for batch in _batches(tasks):
        items.append(_catalog_item(batch, scope="mine", item_id=f"catalog:{batch[0].id}"))
    return items


def _system(db: Session, user: User, since: datetime) -> list[ActivityItem]:
    items: list[ActivityItem] = []

    active, recent = activity.snapshot()
    for entry in active + [r for r in recent if r.finished_at and r.finished_at >= since][:SYSTEM_RECENT_LIMIT]:
        progress = None
        if entry.status != "running":
            progress = 1.0 if entry.status == "success" else None
        elif entry.total:
            progress = min((entry.done or 0) / entry.total, 1.0)
        items.append(
            ActivityItem(
                id=f"system:{entry.key}:{int(entry.started_at.timestamp())}",
                scope="system",
                kind=entry.key,
                title=entry.title,
                subtitle="По расписанию",
                status=entry.status,  # type: ignore[arg-type]
                progress=progress,
                message=entry.message,
                started_at=entry.started_at,
                finished_at=entry.finished_at,
                link="/tenders" if entry.key == "poll_sources" else None,
            )
        )

    # Опрос площадок, запущенный кем-то другим: от него зависит, появятся ли новые закупки.
    polls = list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.kind == JobKind.SOURCES_POLL.value,
                BackgroundJob.status.in_(_ACTIVE_JOB),
                or_(BackgroundJob.created_by_id.is_(None), BackgroundJob.created_by_id != user.id),
            )
        )
    )
    if polls:
        owner_ids = {job.created_by_id for job in polls if job.created_by_id}
        names = {
            row.id: row.full_name
            for row in db.execute(select(User.id, User.full_name).where(User.id.in_(owner_ids))).all()
        } if owner_ids else {}
        items.extend(_job_items(db, polls, scope="system", names=names))

    # Очередь справочника не своего запуска (плановое автозаполнение, чужая кнопка) — одной
    # строкой со шкалой по текущему прогону: от первой ещё не выполненной задачи.
    others = or_(CatalogLookupTask.created_by_id.is_(None), CatalogLookupTask.created_by_id != user.id)
    first_active = db.scalar(
        select(CatalogLookupTask.created_at)
        .where(others, CatalogLookupTask.status.in_(_ACTIVE_CATALOG))
        .order_by(CatalogLookupTask.created_at)
        .limit(1)
    )
    if first_active is not None:
        run = list(
            db.scalars(
                select(CatalogLookupTask).where(
                    others, CatalogLookupTask.created_at >= first_active - CATALOG_BATCH_GAP
                )
            )
        )
        item = _catalog_item(run, scope="system", item_id="system:catalog_queue")
        item.title = "Справочник продукции"
        item.subtitle = "Очередь опроса каталога"
        if item.progress is None:
            item.progress = 0.0 if item.status == "queued" else None
        items.append(item)
    return items


def feed(db: Session, user: User) -> ActivityFeed:
    since = datetime.now(timezone.utc) - RECENT_WINDOW
    items = _mine(db, user, since) + _system(db, user, since)
    # Идущие — сверху, затем свежие итоги.
    items.sort(
        key=lambda item: (
            item.status not in ("queued", "running"),
            -(item.finished_at or item.started_at).timestamp(),
        )
    )
    return ActivityFeed(items=items)
