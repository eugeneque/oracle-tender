import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRole(str, enum.Enum):
    """Роль пользователя. Расширяемый enum (раздел 3, 7 ТЗ) — новые роли (тендерный
    отдел/продажи/руководство) добавляются как новые значения без миграции типа БД,
    так как колонка `role` хранится как обычная строка, а не нативный enum Postgres."""

    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default=UserRole.USER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Аватар (замечание 17.09.2026) — байтами прямо здесь: картинка уменьшена браузером до
    # 256 px и весит десятки килобайт, а `avatar_updated_at` — признак «аватар есть» и метка
    # для сброса кэша в интерфейсе. Раздача — `GET /users/{id}/avatar`.
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    avatar_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Персональный выбор модели ИИ (18.09.2026): "yandex" | "claude" — см.
    # app/services/ai_provider_service.py. `NULL` — пользователь не выбирал, действует
    # системная модель по умолчанию из `ai_provider_settings`.
    ai_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
