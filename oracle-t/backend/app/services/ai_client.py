"""Единая точка входа ИИ-модуля: `run_structured` уходит к активному провайдеру.

До 18.09.2026 вызывающие сервисы импортировали `run_structured` прямо из `yandex_ai_client`.
Теперь провайдеров три (YandexGPT, а через RouterAI — DeepSeek и с 20.10.2026 Qwen вместо
Claude; у двух последних один клиент, разница только в модели; GigaChat убран 20.10.2026),
и какой из них активен, решает
администратор на странице «Интеграции» (блок «Искусственный интеллект»). Сервисы-потребители (извлечение требований,
оценка по профилю, сводка аналитики и т.д.) об этом не знают: контракт `run_structured`
одинаков у обоих клиентов, а выбор делается здесь на каждый вызов — переключение вступает
в силу немедленно, без перезапуска и без задач «в полёте» на старой модели.

Сюда же перенесена `chunk_text`: нарезка документа под контекстное окно от провайдера
не зависит.

Эмбеддинги (`similarity_service`) и OCR-fallback переключателю не подчиняются и остаются на
Yandex: у Claude нет модели эмбеддингов, а векторы разных моделей несравнимы между собой.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

import pydantic
from sqlalchemy.orm import Session

from app.services import routerai_client, yandex_ai_client
from app.models.log import LogLevel
from app.models.user import User
from app.services.ai_log_service import log_ai_event
from app.services.ai_context import JobCancelled, current_user_id, job_started_at
from app.services.ai_provider_service import (
    PROVIDER_LABELS,
    PROVIDER_YANDEX,
    ROUTERAI_PROVIDERS,
    AiModelDisabledError,
    AiNotConfiguredError,
    AiQuotaExceededError,
    AiUnavailableError,
    disabled_since,
    get_active_provider,
    get_routerai_credentials,
    input_budget_chars as _input_budget_chars,
    is_provider_configured,
    is_provider_enabled,
    requested_provider,
)

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)

__all__ = [
    "AiModelDisabledError",
    "AiNotConfiguredError",
    "AiQuotaExceededError",
    "AiUnavailableError",
    "active_model",
    "chunk_text",
    "fit_chars",
    "input_budget_chars",
    "run_structured",
]


def run_structured(
    db: Session,
    *,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    temperature: float = 0.0,
) -> ResponseT:
    """Structured output (JSON по схеме `response_model`) от активного провайдера.

    Ошибка «ключ не задан» у любого из них — `AiNotConfiguredError`, остальное поднимается как
    есть: вызывающий сервис сам решает, как логировать и изолировать сбой (раздел 5.9 ТЗ).
    Итоговый отказ при этом всегда попадает в журнал ИИ (`ai_log_service`, 14.10.2026) —
    вызывающие сервисы пишут его по-разному или не пишут вовсе. Остановка задачи
    (`JobCancelled`) — не ошибка модели и в журнал ИИ не идёт."""

    try:
        return _run_structured(
            db,
            system_prompt=system_prompt,
            user_text=user_text,
            response_model=response_model,
            temperature=temperature,
        )
    except Exception as exc:
        _log_failure(db, exc, getattr(response_model, "__name__", None))
        raise


def _log_failure(db: Session, exc: Exception, operation: str | None) -> None:
    if isinstance(exc, AiModelDisabledError):
        action, level = "Модель отключена администратором", LogLevel.WARNING
    elif isinstance(exc, AiNotConfiguredError):
        action, level = "Модель не настроена", LogLevel.ERROR
    elif isinstance(exc, AiQuotaExceededError):
        action, level = "Лимит провайдера исчерпан, запасной модели нет", LogLevel.ERROR
    else:
        action, level = "Запрос к модели не выполнен", LogLevel.ERROR
    provider = model = None
    try:
        provider, model = active_model(db)
    except Exception:  # noqa: BLE001 - сессия могла упасть вместе с запросом; журнал важнее
        pass
    log_ai_event(
        db,
        action=action,
        error=exc,
        level=level,
        result="error",
        provider=provider,
        model=model,
        operation=operation,
    )


def _run_structured(
    db: Session,
    *,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    temperature: float = 0.0,
) -> ResponseT:
    _stop_if_disabled_during_job(db)
    _stop_if_cancelled()
    provider = get_active_provider(db)
    if not is_provider_enabled(db, provider):
        raise AiModelDisabledError(
            "Все модели ИИ отключены администратором — разбор недоступен, пока одну из них "
            "не включат на странице «Интеграции»."
        )
    called_at = datetime.now(timezone.utc)
    if provider in ROUTERAI_PROVIDERS:
        try:
            result = routerai_client.run_structured(
                db,
                system_prompt=system_prompt,
                user_text=user_text,
                response_model=response_model,
                temperature=temperature,
                provider=provider,
            )
        except AiQuotaExceededError as exc:
            # Лимит расходов ключа RouterAI исчерпан (28.09.2026: DeepSeek — системная модель,
            # и всё фоновое без автора — автозаполнение каталога, ИИ-отбор новых закупок,
            # ночная достройка — падало сотнями вызовов). Деньги сами не вернутся, повтор
            # бессмыслен; если YandexGPT подключён и не выключен, запрос уходит в него.
            if not is_provider_configured(db, PROVIDER_YANDEX) or not is_provider_enabled(
                db, PROVIDER_YANDEX
            ):
                raise
            _warn_quota_fallback(db, provider, exc)
        else:
            _discard_if_disabled_meanwhile(db, provider, called_at)
            return result
        provider = PROVIDER_YANDEX
    result = yandex_ai_client.run_structured(
        db,
        system_prompt=system_prompt,
        user_text=user_text,
        response_model=response_model,
        temperature=temperature,
    )
    _discard_if_disabled_meanwhile(db, provider, called_at)
    return result


def _stop_if_disabled_during_job(db: Session) -> None:
    """Фоновая задача, начатая до того, как администратор выключил её модель, дальше не
    идёт (29.09.2026: «выключаем модель намертво — текущие запросы в неё останавливаются»).
    Перевести её молча на другую модель нельзя: половина разбора была бы от одной модели,
    половина — от другой, а подпись — от первой. Задача начатая уже после выключения сюда
    не попадает: она с самого начала идёт через замену."""

    started_at = job_started_at()
    if started_at is None:
        return
    user_id = current_user_id()
    wanted = requested_provider(db, db.get(User, user_id) if user_id else None)[0]
    since = disabled_since(db, wanted)
    if since is not None and since > started_at:
        raise JobCancelled(
            f"{PROVIDER_LABELS[wanted]} отключена администратором во время разбора — "
            "задача остановлена. Запустите разбор заново: он пойдёт через другую модель."
        )


def _stop_if_cancelled() -> None:
    """Пользователь убрал идущую задачу из очереди — следующий запрос к модели не уходит.
    Проверка перед каждым запросом: разбор — это десятки запросов, и остановка наступает
    через один ответ модели, а не через 15 минут."""

    from app.core.jobs import raise_if_cancelled  # очередь импортирует сервисы — не на уровне модуля

    raise_if_cancelled()


def _discard_if_disabled_meanwhile(db: Session, provider: str, called_at: datetime) -> None:
    """Запрос уже ушёл, и модель выключили, пока она отвечала. Прервать HTTP-запрос из
    другого потока нельзя, но и ответ выключенной модели использовать нельзя — он
    отбрасывается."""

    since = disabled_since(db, provider)
    if since is None or since <= called_at:
        return
    text = f"{PROVIDER_LABELS[provider]} отключена администратором — её ответ не использован."
    if job_started_at() is not None:
        raise JobCancelled(f"{text} Задача остановлена.")
    raise AiModelDisabledError(text)


_quota_warned_at: float = 0.0


def _warn_quota_fallback(db: Session, provider: str, exc: Exception) -> None:
    """Одно предупреждение в 10 минут, а не на каждый вызов: при исчерпанном лимите их сотни."""

    global _quota_warned_at
    import time

    from loguru import logger

    yandex_ai_client._note_fallback(
        f"{PROVIDER_LABELS.get(provider, provider)} недоступен (исчерпан лимит, отклонён ключ "
        "или нет связи) — "
        "ответ дал YandexGPT"
    )
    if time.monotonic() - _quota_warned_at > 600:
        _quota_warned_at = time.monotonic()
        logger.warning(f"{exc} — запросы переключены на YandexGPT до пополнения лимита")
        log_ai_event(
            db,
            action="Провайдер недоступен — запросы переключены на YandexGPT",
            error=exc,
            level=LogLevel.WARNING,
            result="fallback",
            provider=provider,
        )


def active_model(db: Session) -> tuple[str, str | None]:
    """Провайдер и модель, которые обслужат текущее обращение: `("qwen",
    "qwen/qwen3.8-27b")`. Пишется рядом с результатом (AI-оценка, 25.09.2026), чтобы
    расхождение двух расчётов можно было отнести к смене модели. Модель `None` — у RouterAI не
    заполнен ключ: сам вызов тогда всё равно упадёт с понятной ошибкой."""

    provider = get_active_provider(db)
    if provider in ROUTERAI_PROVIDERS:
        try:
            return provider, get_routerai_credentials(db, provider)[1]
        except AiNotConfiguredError:
            return provider, None
    return provider, yandex_ai_client.DEFAULT_MODEL


def input_budget_chars(db: Session) -> int:
    """Сколько символов (системный промпт + сообщение) помещается в запрос к модели, которая
    обслужит текущее обращение (14.10.2026) — от её окна контекста и потолка ответа
    (`ai_provider_service.get_model_limits`). Подмену на YandexGPT при исчерпанном лимите
    RouterAI это не учитывает: куски документации и карточки уже ограничены сверху так, что
    помещаются и в его окно."""

    return _input_budget_chars(db, get_active_provider(db))


def fit_chars(db: Session, desired: int, *, reserved: int) -> int:
    """Длина куска текста для запроса: `desired`, если помещается в окно активной модели
    вместе с `reserved` (промпт, обвязка сообщения), иначе — сколько помещается. Не меньше
    2000 символов: кусок короче бесполезен, и честнее получить отказ провайдера с понятным
    текстом, чем разобрать документ по строчке."""

    return min(desired, max(2_000, input_budget_chars(db) - reserved))


def chunk_text(text: str, *, max_chars: int, overlap: int = 200) -> list[str]:
    """Режет длинный документ на куски под контекстное окно модели.

    `overlap` — перекрытие между кусками: характеристика может оказаться на стыке
    («Номинальное напряжение:» в конце одного куска, значение — в начале следующего),
    без перекрытия такое значение потерялось бы.
    """

    if max_chars <= 0:
        raise ValueError("max_chars должен быть положительным")
    if len(text) <= max_chars:
        return [text] if text else []

    step = max(1, max_chars - overlap)
    return [
        chunk
        for start in range(0, len(text), step)
        if (chunk := text[start : start + max_chars]).strip()
    ]
