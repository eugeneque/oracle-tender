"""Теги закупок (замечание 17.09.2026): «создавать теги и ставить теги на конкретные тендеры».

Теги общие для команды, а не личные, как избранное: тег — язык отдела («срочно»,
«Россети», «переповерка»), и метка, поставленная одним, должна быть видна остальным —
иначе двое заведут по «срочно» и не увидят меток друг друга. Имя уникально без учёта
регистра (индекс по `lower(name)` в миграции 0045), цвет хранится ключом палитры
интерфейса (`TAG_COLORS`), а не HEX: оттенки — дело интерфейса, а не данных.

Связь с закупкой — отдельной таблицей с составным ключом: одна метка на закупку ставится
один раз, а удаление тега снимает его со всех закупок каскадом.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Ключи палитры — единственный источник допустимых значений `color`; интерфейс красит
# по тем же ключам. Новый оттенок добавляется здесь и в `TAG_COLORS` фронтенда.
TAG_COLORS: tuple[str, ...] = (
    "zinc",
    "red",
    "orange",
    "amber",
    "emerald",
    "sky",
    "indigo",
    "violet",
    "pink",
)
DEFAULT_TAG_COLOR = "zinc"


class TenderTag(Base):
    __tablename__ = "tender_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default=DEFAULT_TAG_COLOR)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenderTagLink(Base):
    __tablename__ = "tender_tag_links"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_tags.id", ondelete="CASCADE"), primary_key=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
