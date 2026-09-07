"""История изменений тендера (раздел 5.6 ТЗ — Этап 7).

Отдельная таблица, а не общий журнал `logs`: журнал пишется по всей системе и ищется по
строковому `action`, а история показывается в карточке конкретного тендера, должна
отдаваться одним индексированным запросом по `tender_id` и хранить пару «было → стало» в
отдельных полях, чтобы интерфейс мог показать изменение, а не разбирать текст. Запись в
`logs` при этом остаётся: раздел 5.9 ТЗ требует, чтобы действия пользователя попадали в
сквозной журнал.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HistoryKind(str, enum.Enum):
    """Что за запись: правка поля или комментарий человека.

    Комментарий хранится здесь же, а не в отдельной таблице, потому что в карточке он
    показывается в общей ленте с правками и всегда в хронологическом порядке рядом с ними —
    две таблицы пришлось бы сливать на каждом запросе."""

    FIELD_CHANGE = "field_change"
    COMMENT = "comment"


class TenderHistoryEntry(Base):
    """Запись истории: кто, что и когда поправил (раздел 5.6 ТЗ).

    `user_id` nullable и без каскада: пользователя могут удалить, но история правок должна
    пережить это — иначе исчезнет обоснование решения по тендеру.
    """

    __tablename__ = "tender_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=HistoryKind.FIELD_CHANGE.value
    )
    # Имя поля модели (`tender_type`, `okpd2_code`, ...) — русская подпись подставляется
    # интерфейсом, чтобы переименование ярлыка не требовало миграции данных.
    field_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
