from datetime import datetime

from pydantic import BaseModel


class YandexAiStudioSettingsOut(BaseModel):
    """Ключ никогда не возвращается в открытом виде — только замаскированный хвост
    (`api_key_masked`) и флаг `is_configured`, чтобы фронтенд знал, есть ли значение,
    не показывая его целиком."""

    is_configured: bool
    api_key_masked: str | None
    folder_id: str | None
    updated_at: datetime | None
    updated_by: str | None


class YandexAiStudioSettingsUpdate(BaseModel):
    """`None`/отсутствующее поле = не менять текущее значение (PATCH-семантика) — поле
    определяется через `model_fields_set` в `app/services/yandex_ai_service.py`, чтобы отличить
    «не прислали» от «прислали пустую строку» (последнее — явная очистка значения)."""

    api_key: str | None = None
    folder_id: str | None = None


class YandexConnectionTestResult(BaseModel):
    success: bool
    message: str
