"""Интеграции приборов МИРТЕК в ПО верхнего уровня — данные специалиста (05.11.2026).

Подробности и «зачем» — в докстринге миграции 0092. Коротко: `draft` — то, что специалист
правит, `published` — то, что он подтвердил кнопкой «Сохранить» и что видят анализ тендеров
и каталог. Форма обоих JSON-документов описана в `app/services/software_integration_service.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SoftwareIntegration(Base):
    __tablename__ = "software_integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Стабильный ключ записи («piramida_20», «radioaccess»): по нему идёт первичная загрузка.
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Ключ площадки из app/adapters/upper_software.PLATFORMS, если у ПО есть публичный
    # список: по нему автозаполнение и узнавание ПО в требованиях по названному списку.
    platform_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Шаблоны (регулярные выражения без учёта регистра), по которым ПО узнаётся в тексте
    # требования закупки.
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    published: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    draft_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SoftwareIntegrationDocument(Base):
    """Скан письма или протокола разработчика ПО; из него ИИ предлагает функции и протоколы,
    а специалист подтверждает."""

    __tablename__ = "software_integration_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("software_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # none — не разбирался; pending — в работе; done; failed
    analysis_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    analysis_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SoftwareIntegrationPublication(Base):
    """Журнал нажатий «Сохранить»: кто, когда и что именно изменилось."""

    __tablename__ = "software_integration_publications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
