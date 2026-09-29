import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class BackgroundJobOut(BaseModel):
    """Состояние фоновой задачи для карточки тендера: пока `status` — `queued`/`running`,
    интерфейс показывает индикатор и опрашивает этот же эндпоинт."""

    id: uuid.UUID
    kind: str
    status: str
    tender_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int
    message: str | None
    # Итог задачи без тендера (опрос площадок): результаты по каждой площадке.
    payload: dict | None = None
    # Пока задача ждёт (см. `jobs.queue_info`): `own` — перед ней разборы того же
    # пользователя, `slot` — сервер занят разборами других; `queue_ahead` — сколько их.
    queue_reason: Literal["own", "slot"] | None = None
    queue_ahead: int | None = None


class JobQueueItem(BaseModel):
    """Задача в очереди разборов — для «Моих разборов» и страницы «Логирование»."""

    id: uuid.UUID
    kind: str
    kind_label: str
    status: str
    tender_id: uuid.UUID | None
    tender_title: str | None
    tender_external_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    message: str | None
    queue_reason: Literal["own", "slot"] | None = None
    queue_ahead: int | None = None


class UserJobQueue(BaseModel):
    """Очередь одного пользователя: что разбирается сейчас и что ждёт. `user_id` пустой —
    задачи без автора (расписание, ночная достройка)."""

    user_id: uuid.UUID | None
    user_name: str
    running: list[JobQueueItem]
    queued: list[JobQueueItem]
