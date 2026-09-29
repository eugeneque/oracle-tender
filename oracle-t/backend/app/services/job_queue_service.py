"""Очереди разборов по пользователям (29.09.2026).

С 29.09 у каждого пользователя разбирается одна закупка за раз, остальные его разборы ждут
(`app/core/jobs.py`, `dispatch`). Пользователь видит свою очередь («Мои разборы» в шапке),
администратор на странице «Логирование» — очереди всех и что каждый запускал.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.jobs import queue_info
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.tender import Tender
from app.models.user import User
from app.schemas.job import JobQueueItem, UserJobQueue

KIND_LABELS = {
    JobKind.TENDER_FULL_REVIEW.value: "Полный разбор",
    JobKind.TENDER_ANALYSIS.value: "Анализ документов",
    JobKind.TENDER_EVALUATION.value: "Матрица соответствия",
    JobKind.AI_PROFILE_SCORE.value: "Заключение ИИ",
    JobKind.AI_FEEDBACK.value: "Пересмотр по замечанию",
    JobKind.SOURCES_POLL.value: "Опрос площадок",
}
SYSTEM_NAME = "Система (расписание)"
_ACTIVE = [JobStatus.QUEUED.value, JobStatus.RUNNING.value]


def _items(db: Session, jobs: list[BackgroundJob]) -> list[JobQueueItem]:
    tender_ids = {job.tender_id for job in jobs if job.tender_id}
    tenders = {
        row.id: row
        for row in db.execute(
            select(Tender.id, Tender.title, Tender.external_id).where(Tender.id.in_(tender_ids))
        ).all()
    } if tender_ids else {}
    info = queue_info(db, jobs)
    items = []
    for job in jobs:
        tender = tenders.get(job.tender_id) if job.tender_id else None
        reason, ahead = info.get(job.id, (None, None))
        items.append(
            JobQueueItem(
                id=job.id,
                kind=job.kind,
                kind_label=KIND_LABELS.get(job.kind, job.kind),
                status=job.status,
                tender_id=job.tender_id,
                tender_title=tender.title if tender else None,
                tender_external_id=tender.external_id if tender else None,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                message=job.message,
                queue_reason=reason,
                queue_ahead=ahead,
            )
        )
    return items


def user_queues(db: Session, *, user_id: uuid.UUID | None = None, everyone: bool) -> list[UserJobQueue]:
    """Идущие и ожидающие разборы — одного пользователя или всех (`everyone`). Порядок
    ожидающих — тот, в котором их возьмёт сервер; пользователи — по времени самого раннего
    разбора, у кого он идёт дольше, тот выше."""

    query = select(BackgroundJob).where(
        BackgroundJob.status.in_(_ACTIVE),
        BackgroundJob.kind != JobKind.SOURCES_POLL.value,
    )
    if not everyone:
        query = query.where(BackgroundJob.created_by_id == user_id)
    jobs = list(db.scalars(query.order_by(BackgroundJob.created_at, BackgroundJob.id)))
    items = {item.id: item for item in _items(db, jobs)}

    grouped: dict[uuid.UUID | None, list[BackgroundJob]] = {}
    for job in jobs:
        grouped.setdefault(job.created_by_id, []).append(job)
    names = {
        row.id: row.full_name
        for row in db.execute(
            select(User.id, User.full_name).where(
                User.id.in_([key for key in grouped if key is not None])
            )
        ).all()
    } if any(key is not None for key in grouped) else {}

    return [
        UserJobQueue(
            user_id=owner,
            user_name=names.get(owner, "Удалённый пользователь") if owner else SYSTEM_NAME,
            running=[items[job.id] for job in owner_jobs if job.status == JobStatus.RUNNING.value],
            queued=[items[job.id] for job in owner_jobs if job.status == JobStatus.QUEUED.value],
        )
        for owner, owner_jobs in grouped.items()
    ]


def launched_by(db: Session, user_id: uuid.UUID | None, *, limit: int = 50) -> list[JobQueueItem]:
    """Что пользователь запускал — последние задачи любого статуса, свежие первыми.
    `None` — задачи без автора (расписание)."""

    condition = (
        BackgroundJob.created_by_id.is_(None)
        if user_id is None
        else BackgroundJob.created_by_id == user_id
    )
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(condition)
            .order_by(BackgroundJob.created_at.desc())
            .limit(limit)
        )
    )
    return _items(db, jobs)
