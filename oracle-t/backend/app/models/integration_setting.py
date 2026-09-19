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


class AiProviderSettings(Base):
    """Какая модель обслуживает ИИ-модуль и учётные данные второго провайдера — Claude через
    RouterAI (OpenAI-совместимый шлюз). Появилось 18.09.2026 по просьбе заказчика: до этого
    единственным провайдером был YandexGPT, и переключаться было не на что.

    `active_provider` — переключатель. Активен ровно один провайдер: все запросы генерации
    (`app/services/ai_client.py`) идут через него, прежний в этот момент не вызывается.
    Учётные данные Yandex по-прежнему лежат в `yandex_ai_studio_settings` — на них, помимо
    генерации, держатся OCR-fallback, эмбеддинги «похожих» и Яндекс-поиск, которые от
    переключателя не зависят (у Claude нет ни OCR, ни модели эмбеддингов).

    Таблица — синглтон, как и соседняя (см. `app/services/ai_provider_service.py`)."""

    __tablename__ = "ai_provider_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # "yandex" | "claude" — см. AiProvider в app/services/ai_provider_service.py.
    active_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="yandex")
    routerai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Путь до модели в терминах RouterAI («anthropic/claude-opus-5»). Настраивается, чтобы
    # смена поколения Claude не требовала правки кода — как и `DEFAULT_MODEL` у Yandex.
    routerai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    routerai_base_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class RusprofileSettings(Base):
    """Учётная запись rusprofile.ru (18.09.2026). Под ней система читает то, что без подписки
    скрыто: полный список госзакупок компании с проигрышами, лицензии, численность, финансы —
    и заполняет ими раздел «Моя компания» вместо ручного ввода (см.
    `app/services/rusprofile_service.py`).

    Хранится в БД по тем же соображениям, что и ключи ИИ: пароль меняет заказчик из
    интерфейса, а не администратор сервера. Таблица — синглтон.

    `last_sync_*` — итог последней синхронизации: интерфейс показывает его рядом с кнопкой,
    чтобы было видно, когда данные обновлялись и чем закончилась попытка."""

    __tablename__ = "rusprofile_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "ok" | "error" — и текст, который увидит администратор.
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
