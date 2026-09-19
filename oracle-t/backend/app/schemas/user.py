import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import UserRole


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=8, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    role: UserRole = UserRole.USER


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: UserRole | None = None
    password: str | None = Field(default=None, min_length=8, max_length=255)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    full_name: str
    role: UserRole
    is_active: bool
    # Когда загружен аватар; `None` — аватара нет. Сами байты в ответ не входят: их отдаёт
    # `GET /users/{id}/avatar`, а эта метка нужна интерфейсу, чтобы перечитать картинку.
    avatar_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MeOut(UserOut):
    # Личный выбор модели ИИ; `None` — действует системная по умолчанию.
    ai_provider: str | None = None


class MeUpdate(BaseModel):
    """Что пользователь меняет у себя сам (замечание 17.09.2026): только имя. Логин и
    пароль сознательно не здесь — их меняет администратор в разделе «Пользователи»."""

    full_name: str = Field(min_length=1, max_length=255)
