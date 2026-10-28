import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
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
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BitrixAiRequest(Base):
    """Пакет ИИ-анализа, отправленный в обработчик Bitrix24 `receive.php` (20.10.2026).

    Тело хранится дословно: повтор технически не принятого запроса по инструкции портала
    уходит с тем же `request_id` и теми же байтами (подпись считается от тела), а HTTP-код
    и ответ портала нужны администратору Bitrix24 при разборе ошибки. Секрет здесь не
    хранится — только подписанное им тело."""

    __tablename__ = "bitrix_ai_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[str] = mapped_column(String(191), nullable=False, unique=True)
    analysis_id: Mapped[str] = mapped_column(String(191), nullable=False)
    # 0 — сделку ищет или создаёт портал по `lookup`.
    deal_id_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    full_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    items_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deal_created: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
