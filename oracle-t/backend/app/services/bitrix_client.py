"""Клиент REST API Bitrix24 через входящий вебхук (08.10.2026).

Вебхук — адрес вида `https://<портал>/rest/<id пользователя>/<токен>/`; метод дописывается к
нему (`crm.deal.add.json`), параметры уходят JSON-телом. OAuth и локальное приложение не
нужны: вебхук заводит администратор портала, права у него — права этого пользователя.

Модуль не знает ни про тендеры, ни про базу — только как вызвать метод и разобрать ответ.
Что именно отправлять, решает `bitrix_deal_service`.

Правила, которые здесь важны:

1. **Токен не попадает ни в журнал, ни в текст ошибки.** Вебхук равносилен паролю к CRM, а
   ошибки показываются в интерфейсе и пишутся в журнал — поэтому наружу идёт только адрес
   портала (`portal`), а `httpx`-исключения пересобираются без URL.
2. **Лимит частоты — повтор, а не ошибка.** Портал отвечает `QUERY_LIMIT_EXCEEDED` (HTTP 503),
   когда запросов больше ~2 в секунду; такой ответ лечится паузой.
3. **Ошибка портала — текстом портала.** `error_description` объясняет, что не так (нет
   прав, нет поля, неверная стадия), и показывается администратору как есть.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from loguru import logger

DEFAULT_TIMEOUT_SECONDS = 30.0
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5

# https://портал/rest/<пользователь>/<токен>/ — портал может быть и облачным (*.bitrix24.ru),
# и коробочным на своём домене, как у МИРТЕК.
_WEBHOOK_RE = re.compile(r"^(https://[^/\s]+)/rest/(\d+)/([A-Za-z0-9]+)/?$")

# Коды ответа портала, при которых имеет смысл подождать и повторить.
_RETRY_ERRORS = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"}


class BitrixError(RuntimeError):
    """Портал недоступен или вернул ошибку. Текст — для человека, без токена."""


class BitrixWebhookFormatError(BitrixError):
    """Адрес вебхука не похож на вебхук."""


def normalize_webhook(value: str) -> str:
    """Проверяет адрес вебхука и приводит его к виду с завершающим слешем.

    Типичная ошибка — вставить адрес с методом на конце (`…/profile.json`, так его показывает
    Bitrix24 в примере вызова) или по http. Такие адреса не принимаются с объяснением, а не
    сохраняются молча, чтобы не выяснять это потом по ошибке отправки.
    """

    url = (value or "").strip()
    match = _WEBHOOK_RE.match(url)
    if match is None:
        raise BitrixWebhookFormatError(
            "Адрес вебхука должен выглядеть так: https://портал/rest/<номер пользователя>/"
            "<код>/ — без названия метода на конце."
        )
    return f"{match.group(1)}/rest/{match.group(2)}/{match.group(3)}/"


def portal_of(webhook: str) -> str:
    """Адрес портала без токена — для журнала и сообщений."""

    match = _WEBHOOK_RE.match(webhook.strip())
    return match.group(1) if match else "портал Bitrix24"


def mask_webhook(webhook: str) -> str:
    match = _WEBHOOK_RE.match(webhook.strip())
    if match is None:
        return "•" * 8
    return f"{match.group(1)}/rest/{match.group(2)}/{'•' * 8}/"


class BitrixClient:
    def __init__(
        self,
        webhook: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._webhook = normalize_webhook(webhook)
        self.portal = portal_of(self._webhook)
        # `transport` — для тестов (httpx.MockTransport): в боевой портал тесты не ходят.
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BitrixClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Вызов метода; возвращает `result` из ответа портала."""

        payload = self.call_raw(method, params)
        return payload.get("result")

    def call_raw(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """То же, но целиком — с `total` и `next` для постраничных списков."""

        url = f"{self._webhook}{method}.json"
        last_error = ""
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                response = self._http.post(url, json=params or {})
            except httpx.TimeoutException:
                last_error = f"{self.portal} не ответил за {DEFAULT_TIMEOUT_SECONDS:.0f} с"
            except httpx.HTTPError as exc:
                # Текст httpx-ошибки содержит URL с токеном — наружу только тип ошибки.
                raise BitrixError(
                    f"Не удалось связаться с {self.portal}: {type(exc).__name__}"
                ) from None
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = None

                if isinstance(body, dict) and "error" in body:
                    code = str(body.get("error"))
                    description = str(body.get("error_description") or "").strip()
                    if code in _RETRY_ERRORS:
                        last_error = f"{self.portal}: {description or code}"
                    else:
                        raise BitrixError(_describe_error(code, description, method))
                elif response.status_code >= 500:
                    last_error = f"{self.portal} вернул HTTP {response.status_code}"
                elif response.status_code >= 400 or not isinstance(body, dict):
                    raise BitrixError(
                        f"{self.portal} вернул HTTP {response.status_code} на {method}"
                    )
                else:
                    return body

            if attempt < RETRY_ATTEMPTS:
                logger.info(f"Bitrix24 {method}: {last_error}, повтор {attempt + 1}")
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        raise BitrixError(last_error or f"{self.portal} не выполнил {method}")


def _describe_error(code: str, description: str, method: str) -> str:
    # Самые частые случаи — по-русски с подсказкой; остальное — текстом портала.
    if code in {"NO_AUTH_FOUND", "INVALID_CREDENTIALS", "expired_token", "invalid_token"}:
        return "Портал не принял вебхук: он удалён или код неверный. Создайте вебхук заново."
    if code == "insufficient_scope":
        return (
            "У вебхука нет прав на CRM. В настройках вебхука на портале отметьте право «CRM» "
            "(crm)."
        )
    if code == "ERROR_METHOD_NOT_FOUND":
        return f"Портал не знает метод {method}: возможно, у вебхука нет нужных прав."
    return f"Bitrix24 ({method}): {description or code}"
