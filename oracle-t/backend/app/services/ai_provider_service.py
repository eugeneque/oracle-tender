"""Выбор ИИ-провайдера (18.09.2026) и настройки Claude через RouterAI.

ИИ-модуль (раздел 5.4 ТЗ) изначально работал только через YandexGPT. По просьбе заказчика
добавлен второй провайдер — Claude, подключённый через RouterAI (OpenAI-совместимый шлюз).

Выбор — персональный: у каждого пользователя своя модель (`users.ai_provider`), один может
работать с Claude, другой с YandexGPT. Кто сейчас обращается к модели, известно из контекста
(`app/services/ai_context.py`: middleware для HTTP, `run_job` для фоновых задач), поэтому
`run_structured` в `app/services/ai_client.py` не получает пользователя явно. Пользователь без
собственного выбора и задачи без автора (расписание) работают через **модель по умолчанию**
(`ai_provider_settings.active_provider`), которую задаёт администратор на странице
«Интеграции». Всё читается из БД на каждый запрос — переключение действует сразу.

Учётные данные, как и у Yandex, хранятся в БД (`ai_provider_settings`, синглтон), а не в
`.env` — по той же причине: заказчик не хочет заходить на сервер, чтобы поменять ключ.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from loguru import logger

from app.models.integration_setting import AiProviderSettings, YandexAiStudioSettings
from app.models.log import LogLevel
from app.models.user import User
from app.schemas.integration_setting import (
    AiProviderStatus,
    RouterAiSettingsOut,
    RouterAiSettingsUpdate,
    YandexConnectionTestResult,
)
from app.services.ai_context import current_user_id
from app.services.audit import log_action
from app.services.yandex_ai_service import _SINGLETON_ID as _YANDEX_SINGLETON_ID
from app.services.yandex_ai_service import mask_api_key

_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

PROVIDER_YANDEX = "yandex"
PROVIDER_CLAUDE = "claude"
PROVIDER_LABELS = {PROVIDER_YANDEX: "YandexGPT", PROVIDER_CLAUDE: "Claude"}

# Значения по умолчанию для RouterAI. Модель — путь в терминах шлюза («провайдер/модель»),
# адрес — его OpenAI-совместимый API; и то и другое можно переопределить в настройках.
DEFAULT_ROUTERAI_MODEL = "anthropic/claude-opus-5"
DEFAULT_ROUTERAI_BASE_URL = "https://routerai.ru/api/v1"


class AiNotConfiguredError(RuntimeError):
    """У активного провайдера не заполнены учётные данные. Общий предок для ошибок обоих
    провайдеров: вызывающий код (эндпоинты, фоновые задачи) ловит его и показывает
    пользователю понятный текст вместо 500-й, не зная, какая модель сейчас выбрана."""


def _get_or_create(db: Session) -> AiProviderSettings:
    settings = db.get(AiProviderSettings, _SINGLETON_ID)
    if settings is None:
        settings = AiProviderSettings(id=_SINGLETON_ID, active_provider=PROVIDER_YANDEX)
        db.add(settings)
        db.flush()
    return settings


def get_default_provider(db: Session) -> str:
    """Системная модель по умолчанию — для пользователей без собственного выбора и задач по
    расписанию. Неизвестное значение в БД (например, после отката кода) трактуется как
    Yandex — исходное поведение системы."""

    settings = db.get(AiProviderSettings, _SINGLETON_ID)
    if settings is None or settings.active_provider not in PROVIDER_LABELS:
        return PROVIDER_YANDEX
    return settings.active_provider


def resolve_provider(db: Session, user: User | None) -> tuple[str, str]:
    """Провайдер для конкретного пользователя и откуда он взялся: `("claude", "user")` —
    личный выбор, `("yandex", "default")` — системный. Личный выбор, у которого пропали
    учётные данные (администратор очистил ключ уже после выбора), не роняет пользователя:
    он молча получает модель по умолчанию, а в лог уходит предупреждение — в интерфейсе
    это же видно по статусу «не настроено»."""

    if user is not None and user.ai_provider in PROVIDER_LABELS:
        if _is_configured(db, user.ai_provider):
            return user.ai_provider, "user"
        logger.warning(
            f"У пользователя {user.username} выбрана модель {PROVIDER_LABELS[user.ai_provider]}, "
            "но её учётные данные не заполнены — используется модель по умолчанию"
        )
    return get_default_provider(db), "default"


def get_active_provider(db: Session) -> str:
    """Какой провайдер обслуживает текущее обращение к модели: личный выбор пользователя из
    контекста (`ai_context`) или модель по умолчанию, если пользователя нет или он не выбирал."""

    user_id = current_user_id()
    user = db.get(User, user_id) if user_id else None
    return resolve_provider(db, user)[0]


def get_routerai_credentials(db: Session) -> tuple[str, str, str]:
    """Ключ, модель и адрес API RouterAI. Без ключа — `AiNotConfiguredError`: по аналогии с
    `get_credentials` у Yandex, текст ошибки уходит пользователю как есть."""

    settings = db.get(AiProviderSettings, _SINGLETON_ID)
    if settings is None or not settings.routerai_api_key:
        raise AiNotConfiguredError(
            "Подключение к Claude (RouterAI) не настроено: заполните API-ключ в разделе "
            "«Интеграции → Искусственный интеллект»."
        )
    return (
        settings.routerai_api_key,
        settings.routerai_model or DEFAULT_ROUTERAI_MODEL,
        settings.routerai_base_url or DEFAULT_ROUTERAI_BASE_URL,
    )


def _is_configured(db: Session, provider: str) -> bool:
    if provider == PROVIDER_CLAUDE:
        settings = db.get(AiProviderSettings, _SINGLETON_ID)
        return bool(settings and settings.routerai_api_key)
    yandex = db.get(YandexAiStudioSettings, _YANDEX_SINGLETON_ID)
    return bool(yandex and yandex.api_key and yandex.folder_id)


def _model_name(db: Session, provider: str) -> str | None:
    if provider != PROVIDER_CLAUDE:
        return None
    settings = db.get(AiProviderSettings, _SINGLETON_ID)
    return (settings.routerai_model if settings else None) or DEFAULT_ROUTERAI_MODEL


def get_status(db: Session, user: User | None) -> AiProviderStatus:
    """Что обслуживает запросы этого пользователя. Для интерфейса: карточка тендера красит
    блок «Разбор ИИ» и подписывает модель, переключатели показывают текущее положение."""

    provider, source = resolve_provider(db, user)
    default_provider = get_default_provider(db)
    return AiProviderStatus(
        active_provider=provider,
        label=PROVIDER_LABELS[provider],
        model=_model_name(db, provider),
        is_configured=_is_configured(db, provider),
        source=source,
        default_provider=default_provider,
        default_label=PROVIDER_LABELS[default_provider],
        configured_providers=[key for key in PROVIDER_LABELS if _is_configured(db, key)],
    )


def _ensure_switchable(db: Session, provider: str) -> None:
    """На ненастроенный провайдер переключиться нельзя — иначе одним щелчком все ИИ-функции
    разом начали бы падать с «ключ не задан», а причина была бы на другой странице. Сначала
    ключ, потом переключение."""

    if provider not in PROVIDER_LABELS:
        raise ValueError(f"Неизвестный провайдер: {provider}")
    if not _is_configured(db, provider):
        raise AiNotConfiguredError(
            f"Нельзя переключиться на {PROVIDER_LABELS[provider]}: учётные данные не заполнены."
        )


def switch_default_provider(db: Session, provider: str, *, actor: User) -> AiProviderStatus:
    """Меняет системную модель по умолчанию (администратор). Личные выборы пользователей
    не трогает — у кого модель выбрана, тот продолжает работать с ней."""

    _ensure_switchable(db, provider)
    settings = _get_or_create(db)
    previous = settings.active_provider
    settings.active_provider = provider
    settings.updated_by_id = actor.id

    log_action(
        db,
        component="integrations",
        action="switch_default_ai_provider",
        result="success",
        level=LogLevel.INFO,
        details=f"{PROVIDER_LABELS.get(previous, previous)} → {PROVIDER_LABELS[provider]}",
        user_id=actor.id,
    )
    db.commit()
    return get_status(db, actor)


def set_user_provider(db: Session, user: User, provider: str | None) -> AiProviderStatus:
    """Личный выбор пользователя. `None` — сбросить и вернуться к модели по умолчанию."""

    if provider is not None:
        _ensure_switchable(db, provider)
    previous = user.ai_provider
    user.ai_provider = provider

    log_action(
        db,
        component="integrations",
        action="set_user_ai_provider",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"{PROVIDER_LABELS.get(previous or '', 'по умолчанию')} → "
            f"{PROVIDER_LABELS.get(provider or '', 'по умолчанию')}"
        ),
        user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return get_status(db, user)


def to_routerai_out(db: Session, settings: AiProviderSettings) -> RouterAiSettingsOut:
    updated_by_user = db.get(User, settings.updated_by_id) if settings.updated_by_id else None
    return RouterAiSettingsOut(
        is_configured=bool(settings.routerai_api_key),
        api_key_masked=mask_api_key(settings.routerai_api_key) if settings.routerai_api_key else None,
        model=settings.routerai_model or DEFAULT_ROUTERAI_MODEL,
        base_url=settings.routerai_base_url or DEFAULT_ROUTERAI_BASE_URL,
        updated_at=settings.updated_at if settings.routerai_api_key else None,
        updated_by=updated_by_user.full_name if updated_by_user else None,
    )


def get_routerai_settings_out(db: Session) -> RouterAiSettingsOut:
    return to_routerai_out(db, _get_or_create(db))


def update_routerai_settings(
    db: Session, payload: RouterAiSettingsUpdate, *, actor: User
) -> RouterAiSettingsOut:
    """PATCH-семантика: см. `yandex_ai_service.update_settings`. Пустая строка в `model` или
    `base_url` — возврат к значению по умолчанию (в БД пишется NULL), в `api_key` — очистка."""

    settings = _get_or_create(db)
    fields_set = payload.model_fields_set

    if "api_key" in fields_set:
        settings.routerai_api_key = (payload.api_key or "").strip() or None
    if "model" in fields_set:
        settings.routerai_model = (payload.model or "").strip() or None
    if "base_url" in fields_set:
        settings.routerai_base_url = (payload.base_url or "").strip().rstrip("/") or None
    settings.updated_by_id = actor.id

    log_action(
        db,
        component="integrations",
        action="update_routerai_settings",
        result="success",
        level=LogLevel.INFO,
        details=f"Изменены поля: {', '.join(sorted(fields_set)) or '(нет изменений)'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(settings)
    return to_routerai_out(db, settings)


def test_routerai_connection(db: Session, *, actor: User) -> YandexConnectionTestResult:
    """Минимальный запрос к модели (несколько токенов ответа) — единственный способ проверить
    ключ у OpenAI-совместимого шлюза: отдельного «пинга» без списания там нет. Любая ошибка —
    ожидаемый исход проверки, не 500-я."""

    # Импорт внутри функции: клиент сам импортирует этот модуль ради `AiNotConfiguredError`.
    from app.services.routerai_client import ping

    try:
        api_key, model, base_url = get_routerai_credentials(db)
    except AiNotConfiguredError as exc:
        result = YandexConnectionTestResult(success=False, message=str(exc))
    else:
        try:
            answered_model = ping(api_key=api_key, model=model, base_url=base_url)
        except Exception as exc:  # noqa: BLE001 - любая ошибка подключения - исход теста
            result = YandexConnectionTestResult(
                success=False, message=f"Не удалось подключиться: {exc}"
            )
        else:
            result = YandexConnectionTestResult(
                success=True, message=f"Подключение работает, отвечает {answered_model}."
            )

    log_action(
        db,
        component="integrations",
        action="test_routerai_connection",
        result="success" if result.success else "error",
        level=LogLevel.INFO if result.success else LogLevel.WARNING,
        details=result.message,
        user_id=actor.id,
    )
    db.commit()
    return result
