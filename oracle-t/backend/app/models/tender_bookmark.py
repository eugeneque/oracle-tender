"""Избранные закупки (замечание тестировщика 16.09.2026): «выбирать и сохранять в
отдельном разделе интересующие тендеры, чтобы позже вернуться к их рассмотрению».

Отдельная таблица, а не флаг на тендере: избранное — личное, у каждого пользователя своё
(тендерщик отбирает закупки на неделю, руководитель — другие), и один флаг на закупку
означал бы, что все смотрят в один список. Пара «пользователь — тендер» уникальна; заметка
— зачем отложил, — чтобы через неделю не вспоминать.

Избранное показывается **вне фильтров списка по умолчанию**: закупка, отложенная «на
потом», не должна исчезать из раздела оттого, что у неё истёк срок подачи или она не
прошла профиль релевантности, — иначе раздел становится ненадёжным, и в него перестают
класть.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderBookmark(Base):
    __tablename__ = "tender_bookmarks"
    __table_args__ = (
        UniqueConstraint("user_id", "tender_id", name="uq_tender_bookmarks_user_tender"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
