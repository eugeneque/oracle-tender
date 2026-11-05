"""Отслеживание изменений по закупкам и журнал событий для Bitrix24 (05.11.2026).

Bitrix24 завёл раздел «Журнал событий»: при обнаружении изменения мы передаём актуальное
значение в `deal_fields` и событие в массив `events` (`event_id`, `event_type`, `scope`,
`old_value`/`new_value`, `detected_at`…). Чтобы сказать «было → стало», нужен слепок
закупки на прошлую проверку — `tender_monitors.snapshot`; события лежат в `tender_events`
и отправляются отдельным пакетом с пустыми `sections`.

Отслеживаются не все закупки, а только включённые вручную (для пробного прогона заказчика —
5–10 свежих процедур): проверка каждой означает повторный заход на площадку и в ЕИС.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EventDelivery(str, enum.Enum):
    """Где событие по пути в Bitrix24.

    `held` — тип события ещё не согласован с Bitrix24 (код не утверждён): событие записано,
    но не отправляется, пока код не появится в `EVENT_TYPES` как согласованный."""

    PENDING = "pending"
    HELD = "held"
    SENT = "sent"
    ERROR = "error"


class TenderMonitor(Base):
    """Закупка под наблюдением и её слепок на последнюю проверку."""

    __tablename__ = "tender_monitors"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Пусто, пока не снят исходный слепок: первая проверка событий не создаёт — иначе всё
    # содержимое закупки выглядело бы «новым» (в документе Bitrix24: при первичной загрузке
    # изменение не выдумывать).
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenderEvent(Base):
    """Обнаруженное изменение — одна запись «Журнала событий» Bitrix24."""

    __tablename__ = "tender_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Уникальный идентификатор для портала; повторная отправка того же события несёт тот же.
    event_id: Mapped[str] = mapped_column(String(191), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # Показатель, за которым следим (`application_end`, `auction_date`, `doc:<url>`…): по нему
    # новое изменение ссылается на предыдущее (`supersedes_event_id`).
    field_key: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    scope: Mapped[str] = mapped_column(String(12), nullable=False, default="PROCEDURE")
    lot_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_document: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_event_id: Mapped[str | None] = mapped_column(String(191), nullable=True)
    delivery: Mapped[str] = mapped_column(
        String(10), nullable=False, default=EventDelivery.PENDING.value, index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(191), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
