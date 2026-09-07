import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SourceCredentialOut(BaseModel):
    """Учётка в том виде, в каком её показывает интерфейс.

    Пароля здесь нет и не будет — ни в открытом виде, ни шифротекстом: раз API его не
    отдаёт, украденный токен доступа не превращается в пароль от площадки. Вместо него —
    `password_masked` (число точек по длине пароля), чтобы человек видел, что пароль задан.
    """

    id: uuid.UUID
    source_id: uuid.UUID
    source_key: str
    source_name: str
    label: str
    username: str
    password_masked: str
    notes: str | None
    is_active: bool
    updated_at: datetime
    updated_by: str | None


class SourceCredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    label: str = Field(min_length=1, max_length=150)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    notes: str | None = None
    is_active: bool = True


class SourceCredentialUpdate(BaseModel):
    """PATCH-семантика: не присланное поле не трогаем. Пароль без изменений просто не
    отправляется — иначе, чтобы переименовать блок, его пришлось бы вводить заново."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=150)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    is_active: bool | None = None
