import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class YandexAiStudioSettings(Base):
    """Учётные данные для Yandex AI Studio (раздел 5.4 ТЗ — ИИ-модуль, плюс fallback-OCR
    в разделе 5.2). Хранятся в БД, а не в `.env` — по запросу заказчика ключ должен
    обновляться из админ-панели (`/settings`), без правки кода/конфигурации на сервере.

    Таблица — синглтон: всегда ровно одна строка (см. `app/services/yandex_ai_service.py`),
    поэтому отдельного бизнес-ключа не нужно, только `id`."""

    __tablename__ = "yandex_ai_studio_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

