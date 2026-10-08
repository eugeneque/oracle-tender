"""Единая точка входа ИИ-модуля: `run_structured` уходит к активному провайдеру.

До 18.09.2026 вызывающие сервисы импортировали `run_structured` прямо из `yandex_ai_client`.
Теперь провайдеров три (YandexGPT, а через RouterAI — Claude и с 28.09.2026 DeepSeek; у двух
последних один клиент, разница только в модели; с 08.10.2026 заложен GigaChat — свой клиент
`gigachat_client`), и какой из них активен, решает
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

from app.services import gigachat_client, routerai_client, yandex_ai_client
from app.models.user import User
from app.services.ai_context import JobCancelled, current_user_id, job_started_at
from app.services.ai_provider_service import (
    PROVIDER_GIGACHAT,
    PROVIDER_LABELS,
    PROVIDER_YANDEX,
    ROUTERAI_PROVIDERS,
    AiModelDisabledError,
    AiNotConfiguredError,
    AiQuotaExceededError,
    disabled_since,
    get_active_provider,
    get_gigachat_credentials,
    get_routerai_credentials,
    is_provider_configured,
    is_provider_enabled,
    requested_provider,
)

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)

__all__ = [
    "AiModelDisabledError",
    "AiNotConfiguredError",
    "AiQuotaExceededError",
    "active_model",
    "chunk_text",
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
    есть: вызывающий сервис сам решает, как логировать и изолировать сбой (раздел 5.9 ТЗ)."""

    _stop_if_disabled_during_job(db)
    _stop_if_cancelled()
    provider = get_active_provider(db)
    if not is_provider_enabled(db, provider):
        raise AiModelDisabledError(
            "Все модели ИИ отключены администратором — разбор недоступен, пока одну из них "
            "не включат на странице «Интеграции»."
        )
    called_at = datetime.now(timezone.utc)
    if provider in ROUTERAI_PROVIDERS or provider == PROVIDER_GIGACHAT:
        try:
            if provider == PROVIDER_GIGACHAT:
                result = gigachat_client.run_structured(
                    db,
                    system_prompt=system_prompt,
                    user_text=user_text,
                    response_model=response_model,
                    temperature=temperature,
                )
            else:
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
            # бессмыслен; если YandexGPT подключён и не выключен, запрос уходит в него. То же
            # для GigaChat (08.10.2026), когда закончился пакет токенов.
            if not is_provider_configured(db, PROVIDER_YANDEX) or not is_provider_enabled(
                db, PROVIDER_YANDEX
            ):
                raise
            _warn_quota_fallback(provider, exc)
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


def _warn_quota_fallback(provider: str, exc: Exception) -> None:
    """Одно предупреждение в 10 минут, а не на каждый вызов: при исчерпанном лимите их сотни."""

    global _quota_warned_at
    import time

    from loguru import logger

    yandex_ai_client._note_fallback(
        f"{PROVIDER_LABELS.get(provider, provider)} недоступен (исчерпан лимит или баланс) — "
        "ответ дал YandexGPT"
    )
    if time.monotonic() - _quota_warned_at > 600:
        _quota_warned_at = time.monotonic()
        logger.warning(f"{exc} — запросы переключены на YandexGPT до пополнения лимита")


def active_model(db: Session) -> tuple[str, str | None]:
    """Провайдер и модель, которые обслужат текущее обращение: `("claude",
    "anthropic/claude-opus-5")`. Пишется рядом с результатом (AI-оценка, 25.09.2026), чтобы
    расхождение двух расчётов можно было отнести к смене модели. Модель `None` — у Claude не
    заполнен ключ: сам вызов тогда всё равно упадёт с понятной ошибкой."""

    provider = get_active_provider(db)
    if provider == PROVIDER_GIGACHAT:
        try:
            return provider, get_gigachat_credentials(db)[2]
        except AiNotConfiguredError:
            return provider, None
    if provider in ROUTERAI_PROVIDERS:
        try:
            return provider, get_routerai_credentials(db, provider)[1]
        except AiNotConfiguredError:
            return provider, None
    return provider, yandex_ai_client.DEFAULT_MODEL


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
