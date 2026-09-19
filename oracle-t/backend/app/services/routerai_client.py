"""Клиент Claude через RouterAI — OpenAI-совместимый шлюз (`/chat/completions`).

Второй провайдер ИИ-модуля (18.09.2026). Как и `yandex_ai_client`, модуль не знает, *что*
извлекается, только *как* вызвать модель и получить JSON по схеме; выбор между двумя
клиентами делает `app/services/ai_client.py`.

Пишем на `httpx` без пакета `openai`: нужен один эндпоинт с одним форматом тела, а SDK
тянул бы зависимость ради двух строк и скрыл бы точный текст ошибки шлюза, который здесь
важнее всего (ключ, лимит, недоступная модель — всё приходит в теле ответа).

Structured output: `response_format = json_schema` со `strict: true`. Проверено на живом
шлюзе 18.09.2026 — вложенные модели (`$defs`) и типизация полей приходят как надо; в режиме
`json_object` числа возвращались строками. Схема pydantic перед отправкой приводится к
требованиям strict-режима (`_strict_schema`).
"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

import httpx
import pydantic
from loguru import logger
from sqlalchemy.orm import Session

from app.services.ai_provider_service import get_routerai_credentials

DEFAULT_TIMEOUT_SECONDS = 180.0
# Потолок длины ответа. Самые длинные ответы — списки требований из документации (десятки
# пунктов с нормализованным текстом); 16 тыс. токенов покрывают их с запасом, а обрыв по
# лимиту ловится по `finish_reason` и лечится повтором, как у Yandex.
MAX_OUTPUT_TOKENS = 16_000

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3.0

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)


class RouterAiError(RuntimeError):
    """Шлюз ответил ошибкой. Текст — из тела ответа: там причина (неверный ключ, нет средств,
    модель недоступна), а код статуса сам по себе мало что говорит."""


def _strict_schema(schema: Any) -> Any:
    """Приводит JSON Schema pydantic к strict-режиму OpenAI-совместимых API: у каждого объекта
    `additionalProperties: false`, служебные `title` убраны (шлюз их не понимает и в лучшем
    случае игнорирует). Поля у нас и так все обязательные — это требование ещё Yandex."""

    if isinstance(schema, dict):
        cleaned = {key: _strict_schema(value) for key, value in schema.items() if key != "title"}
        if cleaned.get("type") == "object":
            cleaned["additionalProperties"] = False
        return cleaned
    if isinstance(schema, list):
        return [_strict_schema(item) for item in schema]
    return schema


def _message_text(message: dict[str, Any]) -> str:
    """Текст ответа. OpenAI-формат допускает `content` строкой или списком частей — шлюзы
    отдают по-разному в зависимости от модели за ними."""

    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _strip_fences(text: str) -> str:
    """Снимает ```json ... ``` — в strict-режиме модель их не ставит, но при откате шлюза на
    обычный режим ставит, и без этого ответ не распарсится."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _chat_completion(
    *,
    api_key: str,
    model: str,
    base_url: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        body["response_format"] = response_format

    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            payload = response.json()
            detail = json.dumps(payload.get("error", payload), ensure_ascii=False)[:500]
        except ValueError:
            pass
        raise RouterAiError(f"HTTP {response.status_code}: {detail}")
    return response.json()


def _coerce_shape(raw_text: str, response_model: type[pydantic.BaseModel]) -> object:
    """Приводит две типовые «почти правильные» формы ответа к схеме (18.09.2026).

    На схеме с единственным полем-списком (`RequirementsResult.requirements`) Claude через
    RouterAI в части ответов отдаёт голый массив вместо объекта-обёртки, а иногда — объект,
    в котором значение поля не массив, а JSON-строка с ним. Оба ответа содержательно верны,
    и терять кусок документации после трёх минутных повторов из-за формы обёртки нельзя.
    Всё остальное отдаётся как есть — валидация решит.
    """

    data = json.loads(raw_text)
    fields = response_model.model_fields
    if len(fields) != 1:
        return data
    (name, info), = fields.items()
    origin = getattr(info.annotation, "__origin__", None)
    if isinstance(data, list) and origin is list:
        return {name: data}
    if isinstance(data, dict) and isinstance(data.get(name), str) and origin is list:
        try:
            return {name: json.loads(data[name])}
        except ValueError:
            return data
    return data


def run_structured(
    db: Session,
    *,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    temperature: float = 0.0,
) -> ResponseT:
    """Запрос к Claude через RouterAI со structured output — контракт тот же, что у
    `yandex_ai_client.run_structured`: те же аргументы, тот же тип результата, те же повторы."""

    api_key, model, base_url = get_routerai_credentials(db)

    schema = _strict_schema(response_model.model_json_schema())
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": response_model.__name__, "strict": True, "schema": schema},
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    last_error: Exception | None = None
    raw_text = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            payload = _chat_completion(
                api_key=api_key,
                model=model,
                base_url=base_url,
                messages=messages,
                temperature=temperature,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_format=response_format,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            choice = payload["choices"][0]
            raw_text = _strip_fences(_message_text(choice.get("message") or {}))
            if choice.get("finish_reason") == "length":
                raise RouterAiError("ответ оборван по лимиту токенов")
            return response_model.model_validate(_coerce_shape(raw_text, response_model))
        except (pydantic.ValidationError, ValueError) as exc:
            last_error = exc
            logger.warning(
                f"Claude (RouterAI) вернул ответ, не соответствующий схеме "
                f"{response_model.__name__} (попытка {attempt} из {RETRY_ATTEMPTS}): {exc}; "
                f"начало ответа: {raw_text[:300]!r}"
            )
        except Exception as exc:  # noqa: BLE001 - сетевые сбои и лимиты тоже лечатся повтором
            last_error = exc
            logger.warning(
                f"Обращение к Claude (RouterAI) не удалось (попытка {attempt} из "
                f"{RETRY_ATTEMPTS}): {exc}"
            )

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise last_error if last_error else RuntimeError("Обращение к Claude (RouterAI) не выполнено")


def ping(*, api_key: str, model: str, base_url: str) -> str:
    """Проверка подключения: короткий запрос с ответом в несколько токенов. Возвращает имя
    модели, которое назвал шлюз, — чтобы в результате проверки было видно, что путь до модели
    указан верно, а не просто «какой-то ключ подошёл»."""

    payload = _chat_completion(
        api_key=api_key,
        model=model,
        base_url=base_url,
        messages=[{"role": "user", "content": "Ответь одним словом: ок"}],
        temperature=0.0,
        max_tokens=8,
        response_format=None,
        timeout=60.0,
    )
    return str(payload.get("model") or model)
