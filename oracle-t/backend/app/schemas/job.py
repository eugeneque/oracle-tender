import uuid
from datetime import datetime

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
