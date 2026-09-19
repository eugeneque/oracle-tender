"""Единая точка входа ИИ-модуля: `run_structured` уходит к активному провайдеру.

До 18.09.2026 вызывающие сервисы импортировали `run_structured` прямо из `yandex_ai_client`.
Теперь провайдеров два (YandexGPT и Claude через RouterAI), и какой из них активен, решает
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

from typing import TypeVar

import pydantic
from sqlalchemy.orm import Session

from app.services import routerai_client, yandex_ai_client
from app.services.ai_provider_service import (
    PROVIDER_CLAUDE,
    AiNotConfiguredError,
    get_active_provider,
)

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)

__all__ = ["AiNotConfiguredError", "chunk_text", "run_structured"]


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

    if get_active_provider(db) == PROVIDER_CLAUDE:
        return routerai_client.run_structured(
            db,
            system_prompt=system_prompt,
            user_text=user_text,
            response_model=response_model,
            temperature=temperature,
        )
    return yandex_ai_client.run_structured(
        db,
        system_prompt=system_prompt,
        user_text=user_text,
        response_model=response_model,
        temperature=temperature,
    )


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
