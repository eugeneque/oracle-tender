"""Клиент GigaChat (Сбер) — задел под четвёртый провайдер ИИ-модуля (08.10.2026).

Заказчик оформляет доступ к API; модуль написан по публичной документации
(developers.sber.ru/docs/ru/gigachat/api) и **на живом API ещё не проверялся** — после
получения ключа прогнать «Проверить подключение» и разбор одного тендера, места под сверку
помечены «СВЕРИТЬ». Контракт `run_structured` тот же, что у `routerai_client` и
`yandex_ai_client`; выбор между клиентами — в `app/services/ai_client.py`.

Чем GigaChat отличается от OpenAI-совместимого RouterAI:

- **Авторизация в два шага.** «Ключ авторизации» (Base64 от Client ID:Client Secret из личного
  кабинета) меняется на OAuth-сервере на токен доступа, живущий 30 минут. Токен кэшируется в
  процессе и обновляется за минуту до истечения — иначе каждый кусок документации стоил бы
  лишнего запроса к OAuth.
- **Structured output — через функции.** `response_format: json_schema` у GigaChat нет; схема
  ответа передаётся как параметры единственной функции, а `function_call` заставляет модель
  её «вызвать» — аргументы вызова и есть JSON по схеме.
- **Сертификат НУЦ Минцифры.** API Сбера отдаёт сертификат, подписанный Russian Trusted Root CA,
  которого нет в стандартном наборе `certifi`. Путь к PEM с этим корнем задаётся переменной
  окружения `GIGACHAT_CA_BUNDLE` (`.env`); без неё проверка идёт по системному набору, и на сервере без
  установленного корня запрос упадёт с ошибкой SSL — это видно по «Проверить подключение».
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, TypeVar

import httpx
import pydantic
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.ai_provider_service import AiQuotaExceededError, get_gigachat_credentials
from app.services.routerai_client import _coerce_shape, _message_text, _strip_fences

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_TIMEOUT_SECONDS = 180.0
# СВЕРИТЬ: потолок ответа у старших моделей GigaChat; 16 тыс. — как у RouterAI.
MAX_OUTPUT_TOKENS = 16_000
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3.0
# Токен обновляется заранее: запрос, начатый за секунду до истечения, получил бы 401.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)

_token_lock = threading.Lock()
# (ключ, scope) → (токен, момент истечения по time.time()).
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}


class GigaChatError(RuntimeError):
    """API ответил ошибкой. Текст — из тела ответа: там причина (ключ, scope, модель)."""


class GigaChatQuotaError(GigaChatError, AiQuotaExceededError):
    """Закончился пакет токенов или баланс (HTTP 402) — повтор бессмысленен."""


def _verify() -> str | bool:
    return get_settings().gigachat_ca_bundle or True


def reset_token_cache() -> None:
    """Сбрасывает токены — после смены ключа или scope в настройках, и для тестов."""

    with _token_lock:
        _token_cache.clear()


def _raise_for_status(response: httpx.Response, stage: str) -> None:
    if response.status_code < 400:
        return
    detail = response.text[:500]
    try:
        payload = response.json()
        detail = json.dumps(payload.get("message") or payload, ensure_ascii=False)[:500]
    except ValueError:
        pass
    if response.status_code == 402:
        raise GigaChatQuotaError(
            f"GigaChat отказал: закончился пакет токенов или баланс (HTTP 402: {detail}). "
            "Повтор не поможет — пополните баланс в личном кабинете Сбера или выберите другую модель"
        )
    raise GigaChatError(f"{stage}: HTTP {response.status_code}: {detail}")


def _access_token(auth_key: str, scope: str) -> str:
    """Токен доступа из кэша или свежий от OAuth-сервера."""

    cache_key = (auth_key, scope)
    with _token_lock:
        cached = _token_cache.get(cache_key)
        if cached and cached[1] - TOKEN_REFRESH_MARGIN_SECONDS > time.time():
            return cached[0]

        response = httpx.post(
            OAUTH_URL,
            headers={
                "Authorization": f"Basic {auth_key}",
                # Идентификатор запроса обязателен — по нему поддержка Сбера ищет запрос в логах.
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": scope},
            timeout=30.0,
            verify=_verify(),
        )
        _raise_for_status(response, "Получение токена GigaChat")
        payload = response.json()
        token = payload["access_token"]
        # `expires_at` — миллисекунды Unix-времени. СВЕРИТЬ на живом API.
        expires_at = float(payload.get("expires_at") or 0) / 1000 or time.time() + 1800
        _token_cache[cache_key] = (token, expires_at)
        return token


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Подставляет `$defs` на место `$ref`. Поддержку ссылок в параметрах функций
    документация GigaChat не обещает, а вложенные модели у нас есть (требования, разделы)."""

    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return resolve(defs[ref.split("/")[-1]])
            return {key: resolve(value) for key, value in node.items() if key not in ("$defs", "title")}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def _post_chat(base_url: str, token: str, body: dict[str, Any], timeout: float) -> httpx.Response:
    return httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
        verify=_verify(),
    )


def _chat_completion(
    *,
    auth_key: str,
    scope: str,
    model: str,
    base_url: str,
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    full_body = {"model": model, **body}
    response = _post_chat(base_url, _access_token(auth_key, scope), full_body, timeout)
    if response.status_code == 401:
        # Токен отозван раньше срока — один раз берём новый.
        reset_token_cache()
        response = _post_chat(base_url, _access_token(auth_key, scope), full_body, timeout)
    _raise_for_status(response, "GigaChat")
    return response.json()


def _sampling(temperature: float) -> dict[str, Any]:
    """СВЕРИТЬ: по документации `temperature` должна быть больше нуля. Детерминированный
    ответ (наши извлечения идут с 0) — через минимальный `top_p` вместо нулевой температуры."""

    if temperature <= 0:
        return {"top_p": 0.01}
    return {"temperature": temperature}


def _arguments(message: dict[str, Any]) -> str:
    """JSON ответа: аргументы вызова функции или, если модель ответила текстом, сам текст."""

    call = message.get("function_call")
    if isinstance(call, dict) and call.get("arguments") is not None:
        arguments = call["arguments"]
        return arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return _strip_fences(_message_text(message))


def run_structured(
    db: Session,
    *,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    temperature: float = 0.0,
) -> ResponseT:
    """Запрос к GigaChat со structured output — контракт как у `routerai_client.run_structured`."""

    auth_key, scope, model, base_url = get_gigachat_credentials(db)
    function_name = response_model.__name__
    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "functions": [
            {
                "name": function_name,
                "description": "Вернуть результат строго по схеме.",
                "parameters": _inline_refs(response_model.model_json_schema()),
            }
        ],
        "function_call": {"name": function_name},
        "max_tokens": MAX_OUTPUT_TOKENS,
        **_sampling(temperature),
    }

    last_error: Exception | None = None
    raw_text = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            payload = _chat_completion(
                auth_key=auth_key,
                scope=scope,
                model=model,
                base_url=base_url,
                body=body,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise GigaChatError("ответ оборван по лимиту токенов")
            raw_text = _arguments(choice.get("message") or {})
            return response_model.model_validate(_coerce_shape(raw_text, response_model))
        except AiQuotaExceededError:
            raise
        except (pydantic.ValidationError, ValueError) as exc:
            last_error = exc
            logger.warning(
                f"GigaChat вернул ответ, не соответствующий схеме {function_name} "
                f"(попытка {attempt} из {RETRY_ATTEMPTS}): {exc}; начало ответа: {raw_text[:300]!r}"
            )
        except Exception as exc:  # noqa: BLE001 - сетевые сбои и лимиты частоты лечатся повтором
            last_error = exc
            logger.warning(f"Обращение к GigaChat не удалось (попытка {attempt} из {RETRY_ATTEMPTS}): {exc}")

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise last_error if last_error else RuntimeError("Обращение к GigaChat не выполнено")


def ping(*, auth_key: str, scope: str, model: str, base_url: str) -> str:
    """Проверка подключения: токен + ответ в несколько токенов. Возвращает имя модели из
    ответа — видно, что модель указана верно и доступна по договору."""

    payload = _chat_completion(
        auth_key=auth_key,
        scope=scope,
        model=model,
        base_url=base_url,
        body={
            "messages": [{"role": "user", "content": "Ответь одним словом: ок"}],
            "max_tokens": 8,
        },
        timeout=60.0,
    )
    return str(payload.get("model") or model)
