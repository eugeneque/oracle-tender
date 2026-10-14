import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ActivityItem(BaseModel):
    """Строка центра уведомлений в шапке: процесс, его ход и итог.

    `scope`: `mine` — запущенное этим пользователем (по нему звучит сигнал об окончании),
    `system` — общий процесс: плановый опрос, обход сайтов, чужой опрос площадок."""

    id: str
    scope: Literal["mine", "system"]
    kind: str
    title: str
    subtitle: str | None = None
    status: Literal["queued", "running", "success", "error", "cancelled"]
    # Доля выполненного 0…1; пусто — ход неизвестен, интерфейс крутит индикатор без шкалы.
    progress: float | None = None
    message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    link: str | None = None
    # Задача `background_jobs`, которую можно отменить из центра уведомлений.
    job_id: uuid.UUID | None = None
    owner_name: str | None = None


class ActivityFeed(BaseModel):
    items: list[ActivityItem]
