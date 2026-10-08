import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BitrixDealLink(Base):
    """Какой сделке Bitrix24 соответствует тендер (08.10.2026).

    Одна закупка — одна сделка: повторная отправка обновляет поля существующей сделки, а не
    плодит дубль в воронке. Связь хранится у нас, а дополнительно сделка помечена в CRM
    стандартными полями `ORIGINATOR_ID`/`ORIGIN_ID` — если строку здесь потеряли (откат
    базы, ручная чистка), сделка находится поиском и связь восстанавливается.

    `last_error` — текст последней неудачной отправки: виден в интерфейсе, чтобы «почему
    тендера нет в Битриксе» не приходилось искать по журналу."""

    __tablename__ = "bitrix_deal_links"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    deal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
