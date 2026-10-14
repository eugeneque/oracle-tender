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

С 08.10.2026 заложен четвёртый провайдер — GigaChat (Сбер), напрямую через его API, без
RouterAI (`app/services/gigachat_client.py`). Ключ у заказчика ещё оформляется: модель уже есть
в переключателях и на странице «Интеграции», но пока ключ не введён, она «не настроена» и
выбрать её нельзя — как любую модель без учётных данных.

Учётные данные, как и у Yandex, хранятся в БД (`ai_provider_settings`, синглтон), а не в
`.env` — по той же причине: заказчик не хочет заходить на сервер, чтобы поменять ключ.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from loguru import logger

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.integration_setting import AiProviderSettings, YandexAiStudioSettings
from app.models.log import LogLevel
from app.models.user import User
from app.schemas.integration_setting import (
    AiProviderStatus,
    GigaChatSettingsOut,
    GigaChatSettingsUpdate,
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
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_GIGACHAT = "gigachat"
PROVIDER_LABELS = {
    PROVIDER_YANDEX: "YandexGPT",
    PROVIDER_CLAUDE: "Claude",
    PROVIDER_DEEPSEEK: "DeepSeek",
    PROVIDER_GIGACHAT: "GigaChat",
}
# Провайдеры, которые ходят через RouterAI (28.09.2026: к Claude добавился DeepSeek). Ключ
# и адрес шлюза у них общие, отличается только модель.
ROUTERAI_PROVIDERS = (PROVIDER_CLAUDE, PROVIDER_DEEPSEEK)

# Значения по умолчанию для RouterAI. Модель — путь в терминах шлюза («провайдер/модель»),
# адрес — его OpenAI-совместимый API; и то и другое можно переопределить в настройках.
DEFAULT_ROUTERAI_MODEL = "anthropic/claude-opus-5"
# Конкретная версия, а не алиас `~deepseek/deepseek-v4-pro-latest`: смена модели под тем же
# именем незаметно поменяла бы результаты AI-оценки, а рядом с ней пишется имя модели.
DEFAULT_DEEPSEEK_MODEL = "deepseek/deepseek-v4-pro-0813"
DEFAULT_ROUTERAI_BASE_URL = "https://routerai.ru/api/v1"

# GigaChat (08.10.2026). Версия API (scope) задаётся договором: `GIGACHAT_API_PERS` — физлицо,
# `GIGACHAT_API_B2B` — юрлицо по предоплате, `GIGACHAT_API_CORP` — по постоплате. По умолчанию
# B2B: МИРТЕК — юрлицо, а предоплата — самый частый вариант. Модель — старшая из линейки;
# как и у остальных, всё переопределяется в настройках без правки кода.
GIGACHAT_SCOPES = ("GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP")
DEFAULT_GIGACHAT_SCOPE = "GIGACHAT_API_B2B"
DEFAULT_GIGACHAT_MODEL = "GigaChat-2-Max"
DEFAULT_GIGACHAT_BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"


@dataclass(frozen=True)
class ModelLimits:
    """Окно контекста модели и потолок её ответа, в токенах (14.10.2026)."""

    context_tokens: int
    max_output_tokens: int


# Окно и потолок ответа по умолчанию. До 14.10.2026 потолок ответа был зашит в клиенты, а
# предел карточки производителя — в матрицу (70 тыс. символов под окно YandexGPT); на модели
# с окном 32 тыс. (ускоритель с Qwen) запрос с потолком ответа 16 тыс. в окно не помещался.
# Теперь администратор задаёт оба числа на модель, а объём текста в запросе считается от них
# (`input_budget_chars`).
# - YandexGPT: окно 32 768 («number of input tokens must be no more than 32768», 25.09.2026);
#   8000 — потолок ответа моделей YandexGPT: без него длинный JSON обрывался посреди строки.
# - Claude через RouterAI: окно 1 млн по данным RouterAI; ответ 16 тыс. покрывает самые
#   длинные списки требований, обрыв ловится по `finish_reason` и лечится повтором.
# - DeepSeek, GigaChat: 128 тыс. — СВЕРИТЬ по тарифу; ответ — как у Claude.
DEFAULT_MODEL_LIMITS: dict[str, ModelLimits] = {
    PROVIDER_YANDEX: ModelLimits(context_tokens=32_768, max_output_tokens=8_000),
    PROVIDER_CLAUDE: ModelLimits(context_tokens=1_000_000, max_output_tokens=16_000),
    PROVIDER_DEEPSEEK: ModelLimits(context_tokens=128_000, max_output_tokens=16_000),
    PROVIDER_GIGACHAT: ModelLimits(context_tokens=128_000, max_output_tokens=16_000),
}
MIN_CONTEXT_TOKENS = 4_096
MAX_CONTEXT_TOKENS = 2_000_000
MIN_OUTPUT_TOKENS = 256

# Сколько символов нашего текста приходится на токен — для перевода окна в объём текста.
# Оценка снизу: на русском техническом тексте YandexGPT принимал карточку в 70 тыс. символов
# с промптом и требованиями (~79 тыс.) в окне 32 768, то есть не меньше 2,4 символа на
# токен. Фактическое отношение по каждой модели видно в «Детализации ИИ» (по счётчикам
# провайдеров), и если оно заметно выше — константу можно поднять.
CHARS_PER_TOKEN = 2.4
# Запас на служебные токены разметки сообщений и схему ответа, которые в длину текста не входят.
SAFETY_TOKENS = 1_000


class AiNotConfiguredError(RuntimeError):
    """У активного провайдера не заполнены учётные данные. Общий предок для ошибок обоих
    провайдеров: вызывающий код (эндпоинты, фоновые задачи) ловит его и показывает
    пользователю понятный текст вместо 500-й, не зная, какая модель сейчас выбрана."""


class AiQuotaExceededError(RuntimeError):
    """Провайдер отказал из-за денег: исчерпан лимит расходов ключа или баланс. В отличие от
    сетевого сбоя, повтор тут не поможет — вызывающий код прекращает обращения и показывает
    текст как есть (28.09.2026: ключ RouterAI упёрся в месячный лимит 1 500 руб., а карточка
    писала «модель не вернула ни одного раздела — попробуйте ещё раз»)."""


class AiUnavailableError(AiQuotaExceededError):
    """Провайдер отклонил ключ (HTTP 401/403) — повтор тоже не поможет. Сетевой сбой сюда
    не относится: он бывает разовым, и клиенты повторяют запрос сами. Наследник `AiQuotaExceededError` намеренно — у них одно
    поведение: запрос переключается на YandexGPT, если тот подключён, а фоновые разборы
    прекращают обращения после первого же отказа. До 14.10.2026 такой отказ считался сбоем
    одного куска: разбор каталога с отклонённым ключом сделал 1 485 обращений за 2,5 часа,
    по одному на каждый кусок каждого документа."""


class AiModelDisabledError(AiNotConfiguredError):
    """Модель выключена администратором, а замены ей нет — все остальные тоже выключены или
    не настроены. Наследник `AiNotConfiguredError`: вызывающий код уже показывает его текст
    пользователю как есть."""


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


def get_disabled_providers(db: Session) -> dict[str, dict]:
    """Модели, выключенные администратором: провайдер → `{"at": ISO, "by": ФИО}`."""

    # Столбец отдельным SELECT, а не `db.get`: сессия фоновой задачи живёт десятки минут, и
    # закэшированный в ней объект не увидел бы выключения, сделанного администратором.
    raw = (
        db.execute(
            select(AiProviderSettings.disabled_providers).where(
                AiProviderSettings.id == _SINGLETON_ID
            )
        ).scalar_one_or_none()
        or {}
    )
    return {key: value for key, value in raw.items() if key in PROVIDER_LABELS}


def is_provider_enabled(db: Session, provider: str) -> bool:
    return provider not in get_disabled_providers(db)


def disabled_since(db: Session, provider: str) -> datetime | None:
    """Когда модель выключили; `None` — включена."""

    info = get_disabled_providers(db).get(provider)
    if info is None:
        return None
    try:
        return datetime.fromisoformat(info["at"])
    except (KeyError, TypeError, ValueError):
        # Метка без времени — выключена «всегда»: задачи с ней останавливаются.
        return datetime.min.replace(tzinfo=timezone.utc)


def requested_provider(db: Session, user: User | None) -> tuple[str, str]:
    """Какую модель пользователь хочет — без учёта выключенных администратором:
    `("claude", "user")` — личный выбор, `("yandex", "default")` — системный. Личный выбор,
    у которого пропали учётные данные (администратор очистил ключ уже после выбора), не
    роняет пользователя: он молча получает модель по умолчанию, а в лог уходит
    предупреждение — в интерфейсе это же видно по статусу «не настроено»."""

    if user is not None and user.ai_provider in PROVIDER_LABELS:
        if _is_configured(db, user.ai_provider):
            return user.ai_provider, "user"
        logger.warning(
            f"У пользователя {user.username} выбрана модель {PROVIDER_LABELS[user.ai_provider]}, "
            "но её учётные данные не заполнены — используется модель по умолчанию"
        )
    return get_default_provider(db), "default"


def resolve_provider(db: Session, user: User | None) -> tuple[str, str]:
    """Провайдер, который обслужит запросы пользователя, и откуда он взялся.

    Выключенная администратором модель (29.09.2026) не обслуживает никого: вместо неё —
    модель по умолчанию, а если выключена и она — первая включённая и настроенная. Если
    не осталось ни одной, возвращается запрошенная: сам вызов модели откажет с
    `AiModelDisabledError` и понятным текстом."""

    provider, source = requested_provider(db, user)
    disabled = get_disabled_providers(db)
    if provider not in disabled:
        return provider, source
    default = get_default_provider(db)
    if default not in disabled and _is_configured(db, default):
        return default, "default"
    for key in PROVIDER_LABELS:
        if key not in disabled and _is_configured(db, key):
            return key, "default"
    return provider, source


def get_active_provider(db: Session) -> str:
    """Какой провайдер обслуживает текущее обращение к модели: личный выбор пользователя из
    контекста (`ai_context`) или модель по умолчанию, если пользователя нет или он не выбирал."""

    user_id = current_user_id()
    user = db.get(User, user_id) if user_id else None
    return resolve_provider(db, user)[0]


def _routerai_model(settings: AiProviderSettings | None, provider: str) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return (settings.deepseek_model if settings else None) or DEFAULT_DEEPSEEK_MODEL
    return (settings.routerai_model if settings else None) or DEFAULT_ROUTERAI_MODEL


def get_routerai_credentials(
    db: Session, provider: str = PROVIDER_CLAUDE
) -> tuple[str, str, str]:
    """Ключ, модель и адрес API RouterAI для Claude или DeepSeek. Без ключа —
    `AiNotConfiguredError`: по аналогии с `get_credentials` у Yandex, текст ошибки уходит
    пользователю как есть."""

    settings = db.get(AiProviderSettings, _SINGLETON_ID)
    if settings is None or not settings.routerai_api_key:
        raise AiNotConfiguredError(
            f"Подключение к {PROVIDER_LABELS.get(provider, provider)} (RouterAI) не настроено: "
            "заполните API-ключ в разделе «Интеграции → Искусственный интеллект»."
        )
    return (
        settings.routerai_api_key,
        _routerai_model(settings, provider),
        settings.routerai_base_url or DEFAULT_ROUTERAI_BASE_URL,
    )


def get_gigachat_credentials(db: Session) -> tuple[str, str, str, str]:
    """Ключ авторизации (расшифрованный), scope, модель и адрес API GigaChat. Без ключа —
    `AiNotConfiguredError` с текстом для пользователя, как у остальных провайдеров."""

    settings = db.get(AiProviderSettings, _SINGLETON_ID)
    if settings is None or not settings.gigachat_auth_key_encrypted:
        raise AiNotConfiguredError(
            "Подключение к GigaChat не настроено: заполните ключ авторизации в разделе "
            "«Интеграции → Искусственный интеллект»."
        )
    return (
        decrypt_secret(settings.gigachat_auth_key_encrypted),
        settings.gigachat_scope or DEFAULT_GIGACHAT_SCOPE,
        settings.gigachat_model or DEFAULT_GIGACHAT_MODEL,
        settings.gigachat_base_url or DEFAULT_GIGACHAT_BASE_URL,
    )


def _is_configured(db: Session, provider: str) -> bool:
    if provider in ROUTERAI_PROVIDERS:
        settings = db.get(AiProviderSettings, _SINGLETON_ID)
        return bool(settings and settings.routerai_api_key)
    if provider == PROVIDER_GIGACHAT:
        settings = db.get(AiProviderSettings, _SINGLETON_ID)
        return bool(settings and settings.gigachat_auth_key_encrypted)
    yandex = db.get(YandexAiStudioSettings, _YANDEX_SINGLETON_ID)
    return bool(yandex and yandex.api_key and yandex.folder_id)


def is_provider_configured(db: Session, provider: str) -> bool:
    return _is_configured(db, provider)


# --- окно контекста (14.10.2026) -----------------------------------------------------------


def _stored_limits(db: Session | None) -> dict:
    # Отдельным SELECT, как `get_disabled_providers`: фоновая задача живёт долго, а правка
    # окна администратором должна действовать со следующего запроса. Без сессии (клиент
    # модели вызван вне БД, как в тестах фильтра Yandex) — значения по умолчанию.
    if db is None:
        return {}
    return (
        db.execute(
            select(AiProviderSettings.model_limits).where(AiProviderSettings.id == _SINGLETON_ID)
        ).scalar_one_or_none()
        or {}
    )


def get_model_limits(db: Session | None, provider: str) -> ModelLimits:
    """Окно и потолок ответа модели: заданные администратором поверх значений по умолчанию.
    Испорченное значение в БД (не число) молча заменяется умолчанием — из-за него не должен
    вставать весь разбор."""

    default = DEFAULT_MODEL_LIMITS.get(provider, DEFAULT_MODEL_LIMITS[PROVIDER_YANDEX])
    stored = _stored_limits(db).get(provider) or {}

    def _value(key: str, fallback: int) -> int:
        try:
            return int(stored.get(key) or fallback)
        except (TypeError, ValueError):
            return fallback

    return ModelLimits(
        context_tokens=_value("context_tokens", default.context_tokens),
        max_output_tokens=_value("max_output_tokens", default.max_output_tokens),
    )


def input_budget_chars(db: Session, provider: str) -> int:
    """Сколько символов текста (системный промпт + сообщение) помещается в один запрос к
    модели: окно минус потолок ответа и служебный запас, в пересчёте на символы. Потолок
    ответа вычитается целиком: у OpenAI-совместимых серверов (vLLM на ускорителе) вход и
    `max_tokens` делят одно окно, и запрос, где их сумма больше окна, отклоняется сразу."""

    limits = get_model_limits(db, provider)
    tokens = limits.context_tokens - limits.max_output_tokens - SAFETY_TOKENS
    return max(0, int(tokens * CHARS_PER_TOKEN))


def limits_overview(db: Session) -> list[dict]:
    """Окно и потолок ответа всех моделей для страницы «Интеграции»: текущие, по умолчанию и
    сколько символов это даёт на запрос."""

    stored = _stored_limits(db)
    rows = []
    for provider, label in PROVIDER_LABELS.items():
        limits = get_model_limits(db, provider)
        default = DEFAULT_MODEL_LIMITS[provider]
        rows.append(
            {
                "provider": provider,
                "label": label,
                "context_tokens": limits.context_tokens,
                "max_output_tokens": limits.max_output_tokens,
                "default_context_tokens": default.context_tokens,
                "default_max_output_tokens": default.max_output_tokens,
                "is_default": provider not in stored,
                "input_budget_chars": input_budget_chars(db, provider),
            }
        )
    return rows


def update_model_limits(
    db: Session,
    provider: str,
    *,
    context_tokens: int | None,
    max_output_tokens: int | None,
    actor: User,
) -> list[dict]:
    """Задаёт окно и потолок ответа модели. Оба `None` — вернуть значения по умолчанию.
    Ошибки проверки — `ValueError` с текстом для пользователя: окно меньше потолка ответа
    оставило бы на сам текст ноль символов, и каждый разбор падал бы уже у провайдера."""

    if provider not in PROVIDER_LABELS:
        raise ValueError(f"Неизвестный провайдер: {provider}")
    settings = _get_or_create(db)
    limits = dict(settings.model_limits or {})
    if context_tokens is None and max_output_tokens is None:
        limits.pop(provider, None)
        details = f"{PROVIDER_LABELS[provider]}: окно контекста — по умолчанию"
    else:
        default = DEFAULT_MODEL_LIMITS[provider]
        context = context_tokens or default.context_tokens
        output = max_output_tokens or default.max_output_tokens
        if not MIN_CONTEXT_TOKENS <= context <= MAX_CONTEXT_TOKENS:
            raise ValueError(
                f"Окно контекста — от {MIN_CONTEXT_TOKENS:,} до {MAX_CONTEXT_TOKENS:,} токенов".replace(",", " ")
            )
        if output < MIN_OUTPUT_TOKENS or output > context // 2:
            raise ValueError(
                f"Потолок ответа — от {MIN_OUTPUT_TOKENS} токенов и не больше половины окна "
                f"({context // 2:,} токенов): остальное окно нужно под сам текст".replace(",", " ")
            )
        limits[provider] = {"context_tokens": context, "max_output_tokens": output}
        details = f"{PROVIDER_LABELS[provider]}: окно {context}, ответ до {output} токенов"
    # Новый словарь — JSONB без MutableDict изменений внутри не видит.
    settings.model_limits = limits
    settings.updated_by_id = actor.id
    log_action(
        db,
        component="integrations",
        action="update_ai_model_limits",
        result="success",
        level=LogLevel.INFO,
        details=details,
        user_id=actor.id,
    )
    db.commit()
    return limits_overview(db)


def _model_name(db: Session, provider: str) -> str | None:
    if provider == PROVIDER_GIGACHAT:
        settings = db.get(AiProviderSettings, _SINGLETON_ID)
        return (settings.gigachat_model if settings else None) or DEFAULT_GIGACHAT_MODEL
    if provider not in ROUTERAI_PROVIDERS:
        return None
    return _routerai_model(db.get(AiProviderSettings, _SINGLETON_ID), provider)


def get_status(db: Session, user: User | None) -> AiProviderStatus:
    """Что обслуживает запросы этого пользователя. Для интерфейса: карточка тендера красит
    блок «Разбор ИИ» и подписывает модель, переключатели показывают текущее положение."""

    provider, source = resolve_provider(db, user)
    default_provider = get_default_provider(db)
    disabled = get_disabled_providers(db)
    return AiProviderStatus(
        active_provider=provider,
        label=PROVIDER_LABELS[provider],
        model=_model_name(db, provider),
        is_configured=_is_configured(db, provider),
        source=source,
        default_provider=default_provider,
        default_label=PROVIDER_LABELS[default_provider],
        configured_providers=[key for key in PROVIDER_LABELS if _is_configured(db, key)],
        requested_provider=requested_provider(db, user)[0],
        disabled_providers=[key for key in PROVIDER_LABELS if key in disabled],
        disabled_info={
            key: {"at": value.get("at"), "by": value.get("by")} for key, value in disabled.items()
        },
    )


def _ensure_switchable(db: Session, provider: str) -> None:
    """На ненастроенный провайдер переключиться нельзя — иначе одним щелчком все ИИ-функции
    разом начали бы падать с «ключ не задан», а причина была бы на другой странице. Сначала
    ключ, потом переключение."""

    if provider not in PROVIDER_LABELS:
        raise ValueError(f"Неизвестный провайдер: {provider}")
    if not is_provider_enabled(db, provider):
        raise AiModelDisabledError(
            f"{PROVIDER_LABELS[provider]} отключена администратором — выберите другую модель."
        )
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


def set_provider_enabled(
    db: Session, provider: str, enabled: bool, *, actor: User
) -> AiProviderStatus:
    """Включает или выключает модель для всех (администратор, 29.09.2026).

    Выключение — намертво: ни пользователи с личным выбором, ни задачи по расписанию в неё
    больше не ходят (их запросы обслуживает другая модель, см. `resolve_provider`), а
    фоновые задачи, начатые до выключения, останавливаются на следующем обращении к модели
    (`ai_client.run_structured`). Личный выбор пользователей не стирается: после включения
    каждый вернётся к своей модели."""

    if provider not in PROVIDER_LABELS:
        raise ValueError(f"Неизвестный провайдер: {provider}")
    settings = _get_or_create(db)
    disabled = dict(settings.disabled_providers or {})
    if enabled == (provider not in disabled):
        return get_status(db, actor)
    if enabled:
        disabled.pop(provider, None)
    else:
        disabled[provider] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "by": actor.full_name,
        }
    # Новый словарь, а не правка на месте: JSONB без MutableDict изменений внутри не видит.
    settings.disabled_providers = disabled
    settings.updated_by_id = actor.id

    log_action(
        db,
        component="integrations",
        action="enable_ai_provider" if enabled else "disable_ai_provider",
        result="success",
        level=LogLevel.INFO if enabled else LogLevel.WARNING,
        details=(
            f"{PROVIDER_LABELS[provider]} включена"
            if enabled
            else f"{PROVIDER_LABELS[provider]} отключена: новые запросы в неё не отправляются, "
            "начатые задачи останавливаются"
        ),
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
        model=_routerai_model(settings, PROVIDER_CLAUDE),
        deepseek_model=_routerai_model(settings, PROVIDER_DEEPSEEK),
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
    if "deepseek_model" in fields_set:
        settings.deepseek_model = (payload.deepseek_model or "").strip() or None
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

    # Ключ общий, а модели две — проверяются обе: путь до одной может быть указан с ошибкой
    # или модель недоступна на тарифе, и узнать об этом лучше здесь, а не из карточки тендера.
    try:
        get_routerai_credentials(db)
    except AiNotConfiguredError as exc:
        result = YandexConnectionTestResult(success=False, message=str(exc))
    else:
        lines: list[str] = []
        success = True
        for provider in ROUTERAI_PROVIDERS:
            api_key, model, base_url = get_routerai_credentials(db, provider)
            try:
                answered_model = ping(api_key=api_key, model=model, base_url=base_url)
            except Exception as exc:  # noqa: BLE001 - любая ошибка подключения - исход теста
                success = False
                lines.append(f"{PROVIDER_LABELS[provider]}: не удалось подключиться — {exc}")
            else:
                lines.append(f"{PROVIDER_LABELS[provider]}: работает, отвечает {answered_model}.")
        result = YandexConnectionTestResult(success=success, message="\n".join(lines))

    log_action(
        db,
        component="integrations",
        action="test_routerai_connection",
        result="success" if result.success else "error",
        level=LogLevel.INFO if result.success else LogLevel.WARNING,
        details=result.message,
        user_id=actor.id,
    )
    if not result.success:
        # Отказ проверки — тоже ошибка обращения к модели: в журнал ИИ (14.10.2026).
        from app.services.ai_log_service import log_ai_event

        log_ai_event(db, action="Проверка подключения не прошла", error=result.message)
    db.commit()
    return result


# --- GigaChat (Сбер, 08.10.2026) ----------------------------------------------------------


def to_gigachat_out(db: Session, settings: AiProviderSettings) -> GigaChatSettingsOut:
    updated_by_user = db.get(User, settings.updated_by_id) if settings.updated_by_id else None
    masked = None
    if settings.gigachat_auth_key_encrypted:
        try:
            masked = mask_api_key(decrypt_secret(settings.gigachat_auth_key_encrypted))
        except Exception:  # noqa: BLE001 - утрачен ключ шифрования: показываем, что ключ есть
            masked = "••••"
    return GigaChatSettingsOut(
        is_configured=bool(settings.gigachat_auth_key_encrypted),
        auth_key_masked=masked,
        scope=settings.gigachat_scope or DEFAULT_GIGACHAT_SCOPE,
        model=settings.gigachat_model or DEFAULT_GIGACHAT_MODEL,
        base_url=settings.gigachat_base_url or DEFAULT_GIGACHAT_BASE_URL,
        updated_at=settings.updated_at if settings.gigachat_auth_key_encrypted else None,
        updated_by=updated_by_user.full_name if updated_by_user else None,
    )


def get_gigachat_settings_out(db: Session) -> GigaChatSettingsOut:
    return to_gigachat_out(db, _get_or_create(db))


def update_gigachat_settings(
    db: Session, payload: GigaChatSettingsUpdate, *, actor: User
) -> GigaChatSettingsOut:
    """PATCH-семантика, как у `update_routerai_settings`. Неизвестный scope — ошибка сразу,
    а не «401» от Сбера при первом разборе."""

    settings = _get_or_create(db)
    fields_set = payload.model_fields_set

    if "scope" in fields_set:
        scope = (payload.scope or "").strip().upper() or None
        if scope is not None and scope not in GIGACHAT_SCOPES:
            raise ValueError(
                f"Неизвестная версия API GigaChat: {scope}. Допустимо: {', '.join(GIGACHAT_SCOPES)}."
            )
        settings.gigachat_scope = scope
    if "auth_key" in fields_set:
        auth_key = (payload.auth_key or "").strip()
        settings.gigachat_auth_key_encrypted = encrypt_secret(auth_key) if auth_key else None
    if "model" in fields_set:
        settings.gigachat_model = (payload.model or "").strip() or None
    if "base_url" in fields_set:
        settings.gigachat_base_url = (payload.base_url or "").strip().rstrip("/") or None
    settings.updated_by_id = actor.id

    log_action(
        db,
        component="integrations",
        action="update_gigachat_settings",
        result="success",
        level=LogLevel.INFO,
        details=f"Изменены поля: {', '.join(sorted(fields_set)) or '(нет изменений)'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(settings)
    # Новый ключ или scope — старый токен доступа больше не годится.
    from app.services.gigachat_client import reset_token_cache

    reset_token_cache()
    return to_gigachat_out(db, settings)


def test_gigachat_connection(db: Session, *, actor: User) -> YandexConnectionTestResult:
    """Получение токена и короткий запрос к модели — проверяет и ключ, и scope, и модель."""

    from app.services.gigachat_client import ping

    try:
        auth_key, scope, model, base_url = get_gigachat_credentials(db)
        answered_model = ping(auth_key=auth_key, scope=scope, model=model, base_url=base_url)
    except AiNotConfiguredError as exc:
        result = YandexConnectionTestResult(success=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - любая ошибка подключения - исход теста
        result = YandexConnectionTestResult(
            success=False, message=f"GigaChat: не удалось подключиться — {exc}"
        )
    else:
        result = YandexConnectionTestResult(
            success=True, message=f"GigaChat: работает, отвечает {answered_model}."
        )

    log_action(
        db,
        component="integrations",
        action="test_gigachat_connection",
        result="success" if result.success else "error",
        level=LogLevel.INFO if result.success else LogLevel.WARNING,
        details=result.message,
        user_id=actor.id,
    )
    if not result.success:
        # Отказ проверки — тоже ошибка обращения к модели: в журнал ИИ (14.10.2026).
        from app.services.ai_log_service import log_ai_event

        log_ai_event(db, action="Проверка подключения не прошла", error=result.message)
    db.commit()
    return result
