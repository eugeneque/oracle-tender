import uuid
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

# Claude и GigaChat убраны 20.10.2026, добавлен Qwen.
AiProviderKey = Literal["yandex", "deepseek", "qwen"]


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
    # Отключение моделей администратором (29.09.2026). `requested_provider` — что выбрал
    # пользователь (или умолчание); если она выключена, `active_provider` — её замена.
    requested_provider: AiProviderKey | None = None
    disabled_providers: list[AiProviderKey] = []
    # Кто и когда выключил: `{"deepseek": {"at": ISO, "by": ФИО}}`.
    disabled_info: dict[str, dict] = {}


class AiProviderEnabledUpdate(BaseModel):
    enabled: bool


class AutoReviewCurrent(BaseModel):
    job_id: uuid.UUID
    status: str
    message: str | None = None
    tender_id: uuid.UUID
    external_id: str
    title: str


class AutoReviewStatus(BaseModel):
    """Автоматический разбор через YandexGPT (30.10.2026, раздел 81 ARCHITECTURE.md).
    `blocker` — почему очередь стоит при включённом флаге (Yandex не настроена или
    выключена); `pending` — сколько закупок ждут разбора, `current` — какая разбирается."""

    enabled: bool
    provider: AiProviderKey
    blocker: str | None = None
    pending: int
    done: int
    failed: int
    current: AutoReviewCurrent | None = None


class AiModelLimitsOut(BaseModel):
    """Окно контекста и потолок ответа модели (14.10.2026). `input_budget_chars` — сколько
    символов текста это даёт на один запрос: от него режутся куски документации и карточка
    производителя в матрице."""

    provider: AiProviderKey
    label: str
    context_tokens: int
    max_output_tokens: int
    default_context_tokens: int
    default_max_output_tokens: int
    is_default: bool
    input_budget_chars: int


class AiModelLimitsUpdate(BaseModel):
    """Оба поля пустые — вернуть значения по умолчанию."""

    context_tokens: int | None = None
    max_output_tokens: int | None = None


class AiProviderSwitch(BaseModel):
    active_provider: AiProviderKey


class MyAiProviderUpdate(BaseModel):
    """Личный выбор модели; `None` — сбросить к системной по умолчанию."""

    ai_provider: AiProviderKey | None


class RouterAiSettingsOut(BaseModel):
    """Как и у Yandex: ключ наружу не отдаётся, только замаскированный хвост."""

    is_configured: bool
    api_key_masked: str | None
    deepseek_model: str
    # Модель Qwen (20.10.2026) — через тот же ключ и шлюз.
    qwen_model: str
    base_url: str
    updated_at: datetime | None
    updated_by: str | None


class RouterAiSettingsUpdate(BaseModel):
    """PATCH-семантика, как у `YandexAiStudioSettingsUpdate`: не присланное поле не трогаем,
    пустая строка — сброс к значению по умолчанию (для модели и адреса) или очистка (для ключа)."""

    api_key: str | None = None
    deepseek_model: str | None = None
    qwen_model: str | None = None
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


# --- Bitrix24: сделки через входящий вебхук (08.10.2026) ------------------------------------


class Bitrix24SettingsOut(BaseModel):
    """Вебхук наружу не отдаётся: он равносилен паролю к CRM. Только маска без кода и адрес
    портала — по ним видно, куда подключены."""

    is_configured: bool
    webhook_masked: str | None
    portal: str | None
    category_id: int
    stage_id: str
    push_enabled: bool
    updated_at: datetime | None
    updated_by: str | None
    last_check_at: datetime | None
    last_check_status: str | None
    last_check_message: str | None


class Bitrix24SettingsUpdate(BaseModel):
    """PATCH: не присланное поле не трогаем; пустой `webhook_url` — отключение портала."""

    webhook_url: str | None = None
    category_id: int | None = None
    stage_id: str | None = None
    push_enabled: bool | None = None


class Bitrix24Stage(BaseModel):
    id: str
    name: str


class Bitrix24FieldRef(BaseModel):
    key: str
    title: str


class Bitrix24CheckResult(BaseModel):
    success: bool
    message: str
    stages: list[Bitrix24Stage]
    stage_found: bool
    # Поля из маппинга, которых на портале нет: их удалили или переименовали.
    missing_fields: list[Bitrix24FieldRef]


class Bitrix24DealFieldValue(BaseModel):
    key: str
    title: str
    value: str


class Bitrix24DealPreview(BaseModel):
    tender_id: str
    deal_id: int | None
    # "add" — сделки ещё нет, будет создана в выбранной воронке; "update" — обновятся поля.
    action: Literal["add", "update"]
    push_enabled: bool
    fields: list[Bitrix24DealFieldValue]


class Bitrix24PushResult(BaseModel):
    deal_id: int
    created: bool
    url: str
    message: str


# --- Bitrix24: пакеты ИИ-анализа в receive.php (20.10.2026) ------------------------------------


class BitrixAiSettingsOut(BaseModel):
    """Секрет HMAC не отдаётся — только признак и последние четыре знака для сверки."""

    receiver_url: str | None
    secret_set: bool
    secret_hint: str | None
    push_enabled: bool


class BitrixAiSettingsUpdate(BaseModel):
    """PATCH: пустой адрес или секрет — удаление (отправка при этом выключается)."""

    receiver_url: str | None = None
    secret: str | None = None
    push_enabled: bool | None = None


class BitrixAiSendRequest(BaseModel):
    # id тендера или номер закупки, как в списке.
    tender: str
    full_snapshot: bool = False


class BitrixAiPreview(BaseModel):
    tender_id: str
    payload: dict
    items_count: int
    size_bytes: int


class BitrixAiRequestOut(BaseModel):
    id: str
    tender_id: str
    tender_number: str | None
    tender_title: str | None
    request_id: str
    analysis_id: str
    deal_id_sent: int
    full_snapshot: bool
    items_count: int
    http_status: int | None
    response_body: str | None
    accepted: bool
    duplicate: bool
    deal_id: int | None
    deal_created: bool | None
    deal_url: str | None
    error: str | None
    attempts: int
    retryable: bool
    sent_at: datetime | None


class BitrixTenderState(BaseModel):
    """Кнопка «Загрузить в Битрикс» на странице тендера (20.10.2026)."""

    can_upload: bool
    # Почему нельзя — показывается подсказкой у кнопки.
    reason: str | None
    deal_id: int | None
    deal_url: str | None
    last_request: BitrixAiRequestOut | None
