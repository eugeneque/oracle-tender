from datetime import datetime
from typing import Literal

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


# --- переключатель ИИ-провайдера и Claude через RouterAI (18.09.2026) --------------------

AiProviderKey = Literal["yandex", "claude"]


class AiProviderStatus(BaseModel):
    """Какая модель обслуживает запросы текущего пользователя. Доступно любому пользователю:
    карточка тендера красит блок «Разбор ИИ» под неё и подписывает имя, переключатели
    показывают положение. Ключей здесь нет — только названия и флаги «настроено».

    `source` — откуда взялась модель: `user` (личный выбор) или `default` (системная).
    `configured_providers` — на что вообще можно переключиться."""

    active_provider: AiProviderKey
    label: str
    model: str | None
    is_configured: bool
    source: Literal["user", "default"]
    default_provider: AiProviderKey
    default_label: str
    configured_providers: list[AiProviderKey]


class AiProviderSwitch(BaseModel):
    active_provider: AiProviderKey


class MyAiProviderUpdate(BaseModel):
    """Личный выбор модели; `None` — сбросить к системной по умолчанию."""

    ai_provider: AiProviderKey | None


class RouterAiSettingsOut(BaseModel):
    """Как и у Yandex: ключ наружу не отдаётся, только замаскированный хвост."""

    is_configured: bool
    api_key_masked: str | None
    model: str
    base_url: str
    updated_at: datetime | None
    updated_by: str | None


class RouterAiSettingsUpdate(BaseModel):
    """PATCH-семантика, как у `YandexAiStudioSettingsUpdate`: не присланное поле не трогаем,
    пустая строка — сброс к значению по умолчанию (для модели и адреса) или очистка (для ключа)."""

    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


# --- rusprofile.ru (18.09.2026) ----------------------------------------------------------------


class RusprofileSettingsOut(BaseModel):
    """Пароль наружу не отдаётся — только признак, что он задан. Логин показывается: это
    адрес почты учётной записи, по нему администратор понимает, чей это аккаунт."""

    is_configured: bool
    login: str | None
    has_password: bool
    updated_at: datetime | None
    updated_by: str | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_message: str | None


class RusprofileSettingsUpdate(BaseModel):
    """PATCH-семантика, как у остальных интеграций: не присланное поле не трогаем, пустая
    строка — очистка."""

    login: str | None = None
    password: str | None = None


class RusprofileSyncResult(BaseModel):
    """Итог кнопки «Обновить из rusprofile» — что именно изменилось в разделе «Моя компания»."""

    card_id: str
    source_url: str
    profile_fields_updated: list[str]
    licenses_total: int
    projects_total: int
    purchases_fetched: int
    purchases_total_on_site: int | None
    participations_created: int
    participations_updated: int
    wins: int
    losses: int
    # Часть данных на сайте осталась скрытой (нет подписки или она кончилась) — предупреждение
    # рядом с результатом, чтобы «0 проигрышей» не приняли за факт.
    data_hidden: bool
    message: str
