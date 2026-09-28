"""Клиент текстовой генерации Yandex AI Studio / YandexGPT (раздел 5.4, 6.2 ТЗ).

Учётные данные берутся из БД (`yandex_ai_studio_settings`), а не из `.env` — по запросу
заказчика ключ настраивается из админ-панели (см. `app/services/yandex_ai_service.py`).
Этот модуль — тонкая обёртка: он не знает, *что* извлекается (характеристики продукции,
требования тендера и т.д.), только *как* вызвать модель и получить структурированный ответ.
Конкретные промпты живут в вызывающих сервисах.

С 18.09.2026 это один из двух провайдеров: сервисы-потребители зовут `run_structured` из
`app/services/ai_client.py`, который выбирает между этим клиентом и Claude (RouterAI) по
настройке администратора. Напрямую отсюда берут только то, что есть лишь у Yandex:
эмбеддинги и учётные данные для OCR/поиска.
"""

from __future__ import annotations

import re
import time
from contextvars import ContextVar
from typing import TypeVar

import pydantic
from loguru import logger
from sqlalchemy.orm import Session

from app.models.integration_setting import YandexAiStudioSettings
from app.services.ai_provider_service import AiNotConfiguredError
from app.services.yandex_ai_service import _SINGLETON_ID

# Модель по умолчанию. `yandexgpt` (Pro) — а не `yandexgpt-lite`: задачи раздела 5.4-5.5 ТЗ
# (извлечение требований, сопоставление характеристик) требуют аккуратности на длинных
# технических текстах, где lite заметно слабее. Настраивается на случай смены поколения
# модели без правки кода.
DEFAULT_MODEL = "yandexgpt"
DEFAULT_TIMEOUT_SECONDS = 120.0

# Предел длины ответа. Без него действует умолчание платформы, и длинный JSON обрывается
# посреди строки («EOF while parsing», ~9 тыс. символов): на ТЗ с десятками параметров кусок
# документации терялся после трёх попыток (25.09.2026, закупка 32616309303). 8000 —
# потолок ответа моделей YandexGPT.
MAX_OUTPUT_TOKENS = 8000

# Повторные попытки при сбое обращения к модели (раздел 5.9 ТЗ). Сбои здесь двух видов:
# сетевые/квотные (лечатся повтором) и «модель вернула не тот JSON» (лечится тоже — при
# temperature=0 повтор чаще всего даёт валидный ответ, потому что обрыв по лимиту токенов
# зависит от того, насколько многословно модель начала отвечать).
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3.0

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)

# --- отказ по фильтру тематики -------------------------------------------------------------
#
# 28.09.2026, закупка 32616398561 (поверка АИИС КУЭ): на вызове «Заключение» YandexGPT вместо
# JSON отвечал «Я не могу обсуждать эту тему. Давайте поговорим о чём-нибудь ещё.» — ответ
# со статусом CONTENT_FILTER. Отдельного запретного слова нет: фильтр срабатывает на сумму
# «чувствительных» фрагментов вполне делового текста — лицензия ФСБ на шифровальные
# (криптографические) средства из профиля компании плюс выдержки rusprofile про арбитраж,
# исполнительные производства и арест имущества. Убери любую из групп — модель отвечает.
# При temperature=0 повтор даёт тот же отказ, поэтому три попытки лишь тратили время.
#
# Порядок после отказа:
# 1. тот же YandexGPT с деловой преамбулой и смягчёнными формулировками (`soften_for_filter`)
#    — факты те же, меняются слова, на которые реагирует фильтр;
# 2. запасная модель каталога Yandex AI Studio на том же ключе и в том же каталоге
#    (`FILTER_FALLBACK_MODEL`, по умолчанию Qwen3-235B) — через OpenAI-совместимый API,
#    по gRPC она недоступна. У неё свой, заметно менее строгий фильтр; на отказном
#    контексте ответила по схеме за 10 с.

FILTER_FALLBACK_MODEL = "qwen3-235b-a22b-fp8/latest"
OPENAI_COMPATIBLE_URL = "https://llm.api.cloud.yandex.net/v1"

# Отказ бывает и без статуса (старые версии модели, OpenAI-режим отдаёт его текстом).
_REFUSAL_RE = re.compile(
    r"^\s*(я не могу обсуждать|давайте поговорим о чём-нибудь|не могу ответить на (этот|ваш) вопрос)",
    re.IGNORECASE,
)

_FILTER_SAFE_PREFACE = (
    "Контекст работы: деловой анализ открытой государственной или корпоративной закупки "
    "(44-ФЗ, 223-ФЗ) по публичным данным — ЕИС, ЕГРЮЛ, реестры. Лицензии, допуски, судебные "
    "дела, исполнительные производства, реестры и учреждения заказчиков — обычные реквизиты "
    "участников и заказчиков закупок; их нужно учесть как факты, а не обсуждать как тему. "
    "Ответ — только JSON по заданной схеме.\n\n"
)

# Смягчение формулировок: смысл для тендерного вывода сохраняется, пропадают слова-триггеры.
_SOFTENINGS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in (
        (r"шифровальн\w*\s*\(криптографическ\w*\)\s*средств\w*", "средств защиты информации"),
        (r"криптографическ\w*", "защитных"),
        (r"шифровальн\w*", "защитных"),
        (r"взрывопожароопасн\w* и химически опасн\w*", "опасных"),
        (r"исправительн\w* колони\w*", "учреждение"),
        (r"(Исполнительные производства:)[^\n]*", r"\1 есть сведения (подробности опущены)"),
        (r"(Арбитраж:)[^\n]*", r"\1 есть арбитражные дела (подробности опущены)"),
        (r"(Проверки:)[^\n]*", r"\1 есть сведения о проверках (подробности опущены)"),
        (r"наложен\w* арест", "наложены ограничения"),
        (r"\bарест\w*", "ограничения"),
        (r"банкрот\w*", "несостоятельным"),
        (r"ликвидаци\w*", "прекращения деятельности"),
        (r"\bФСБ\b", "регулятора"),
    )
)


class YandexContentFilterError(RuntimeError):
    """YandexGPT отказался отвечать по фильтру тематики. Повтор того же запроса бесполезен."""


# Какие обращения этого потока обслужила не основная модель — чтобы вызывающий сервис мог
# назвать это в итоге задачи («заключение составлено Qwen3 — YandexGPT отказал»).
_fallback_notes: ContextVar[list[str] | None] = ContextVar("yandex_fallback_notes", default=None)


def drain_fallback_notes() -> list[str]:
    """Заметки о запасных путях с прошлого вызова этой функции (в текущем потоке)."""

    notes = _fallback_notes.get() or []
    _fallback_notes.set([])
    return notes


def _note_fallback(text: str) -> None:
    notes = list(_fallback_notes.get() or [])
    if text not in notes:
        notes.append(text)
    _fallback_notes.set(notes)


def soften_for_filter(text: str) -> str:
    for pattern, replacement in _SOFTENINGS:
        text = pattern.sub(replacement, text)
    return text


def is_refusal(text: str) -> bool:
    return bool(_REFUSAL_RE.match(text or ""))


class YandexAiNotConfiguredError(AiNotConfiguredError):
    """Ключ/Folder ID не заданы в админ-панели — вызывающий код должен сообщить об этом
    пользователю понятным текстом, а не падать 500-й ошибкой. Наследует общий
    `AiNotConfiguredError`, чтобы потребители ловили одно исключение независимо от того,
    какой провайдер активен."""


def get_credentials(db: Session) -> tuple[str, str]:
    settings = db.get(YandexAiStudioSettings, _SINGLETON_ID)
    if settings is None or not settings.api_key or not settings.folder_id:
        raise YandexAiNotConfiguredError(
            "Подключение к Yandex AI Studio не настроено: заполните API-ключ и Folder ID "
            "на странице «Интеграции» (блок «Искусственный интеллект»)."
        )
    return settings.api_key, settings.folder_id


def run_structured(
    db: Session,
    *,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    model_name: str = DEFAULT_MODEL,
    temperature: float = 0.0,
) -> ResponseT:
    """Запрос к YandexGPT со structured output (JSON по схеме `response_model`).

    `temperature=0.0` по умолчанию: извлечение фактов из документа — детерминированная задача,
    разброс между запусками здесь вреден (одна и та же документация должна давать один и тот
    же каталог).

    Сбой повторяется до `RETRY_ATTEMPTS` раз (раздел 5.9 ТЗ). Если все попытки провалились,
    исключение поднимается наружу: вызывающий сервис решает, как его логировать и изолировать
    (раздел 5.9 ТЗ), по аналогии с адаптерами источников.
    """

    from yandex_ai_studio_sdk import AIStudio

    api_key, folder_id = get_credentials(db)

    sdk = AIStudio(folder_id=folder_id, auth=api_key)
    model = sdk.models.completions(model_name).configure(
        temperature=temperature, response_format=response_model, max_tokens=MAX_OUTPUT_TOKENS
    )

    try:
        return _run_with_retries(
            model,
            system_prompt=system_prompt,
            user_text=user_text,
            response_model=response_model,
            attempts=RETRY_ATTEMPTS,
        )
    except YandexContentFilterError as refusal:
        logger.warning(
            f"YandexGPT отказал по фильтру тематики на схеме {response_model.__name__} — "
            "пробую деловую формулировку"
        )
        first_refusal = refusal

    # Вариант 1: тот же YandexGPT, деловая преамбула и смягчённые формулировки.
    try:
        answer = _run_with_retries(
            model,
            system_prompt=_FILTER_SAFE_PREFACE + system_prompt,
            user_text=soften_for_filter(user_text),
            response_model=response_model,
            attempts=1,
        )
        _note_fallback("YandexGPT ответил только на смягчённую формулировку (фильтр тематики)")
        return answer
    except YandexContentFilterError:
        logger.warning(
            f"YandexGPT отказал и на деловую формулировку ({response_model.__name__}) — "
            f"запасная модель {FILTER_FALLBACK_MODEL}"
        )
    except Exception as exc:  # noqa: BLE001 - переходим к запасной модели
        logger.warning(f"Деловая формулировка для YandexGPT не удалась: {exc}")

    # Вариант 2: запасная модель каталога Yandex AI Studio, исходный текст целиком.
    try:
        answer = _run_openai_compatible(
            api_key=api_key,
            folder_id=folder_id,
            model_name=FILTER_FALLBACK_MODEL,
            system_prompt=system_prompt,
            user_text=user_text,
            response_model=response_model,
            temperature=temperature,
        )
    except Exception as exc:  # noqa: BLE001
        raise YandexContentFilterError(
            f"YandexGPT отказал по фильтру тематики («{first_refusal}»), запасная модель "
            f"{FILTER_FALLBACK_MODEL} не ответила: {exc}"
        ) from exc
    _note_fallback(
        f"ответ дала запасная модель {FILTER_FALLBACK_MODEL.split('/')[0]} — YandexGPT "
        "отказал по фильтру тематики"
    )
    return answer


def _run_with_retries(
    model,
    *,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    attempts: int,
) -> ResponseT:
    """Обращение к одной модели с повторами. Отказ по фильтру поднимается сразу, без
    повторов: при temperature=0 он воспроизводится один в один."""

    last_error: Exception | None = None
    raw_text = ""
    for attempt in range(1, attempts + 1):
        try:
            result = model.run(
                [
                    {"role": "system", "text": system_prompt},
                    {"role": "user", "text": user_text},
                ],
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            raw_text = result[0].text
            status = getattr(getattr(result[0], "status", None), "name", "")
            if status == "CONTENT_FILTER" or is_refusal(raw_text):
                raise YandexContentFilterError(raw_text.strip()[:120] or status)
            return response_model.model_validate_json(raw_text)
        except YandexContentFilterError:
            raise
        except pydantic.ValidationError as exc:
            # Модель вернула JSON, не подходящий под схему (бывает при обрыве по лимиту токенов).
            # Логируем начало ответа — этого достаточно для диагностики, но не засоряет лог
            # многокилобайтным текстом.
            last_error = exc
            logger.warning(
                f"YandexGPT вернул ответ, не соответствующий схеме {response_model.__name__} "
                f"(попытка {attempt} из {attempts}): {exc}; начало ответа: {raw_text[:300]!r}"
            )
        except Exception as exc:  # noqa: BLE001 - сетевые сбои и квоты тоже лечатся повтором
            last_error = exc
            logger.warning(
                f"Обращение к YandexGPT не удалось (попытка {attempt} из {attempts}): {exc}"
            )

        if attempt < attempts:
            # Линейная задержка, а не экспонента: три попытки с шагом в несколько секунд
            # укладываются в разумное ожидание фоновой задачи, а сбои квоты снимаются и так.
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    # Цикл всегда завершается либо возвратом ответа, либо сохранённой ошибкой.
    raise last_error if last_error else RuntimeError("Обращение к YandexGPT не выполнено")


def _run_openai_compatible(
    *,
    api_key: str,
    folder_id: str,
    model_name: str,
    system_prompt: str,
    user_text: str,
    response_model: type[ResponseT],
    temperature: float,
    attempts: int = 2,
) -> ResponseT:
    """Модель каталога Yandex AI Studio через OpenAI-совместимый API (Qwen, gpt-oss): ключ и
    каталог те же, что у YandexGPT. Тело запроса и разбор ответа — как у RouterAI."""

    from app.services.routerai_client import (
        _chat_completion,
        _coerce_shape,
        _message_text,
        _strict_schema,
        _strip_fences,
    )

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": response_model.__name__,
            "strict": True,
            "schema": _strict_schema(response_model.model_json_schema()),
        },
    }
    last_error: Exception | None = None
    raw_text = ""
    for attempt in range(1, attempts + 1):
        try:
            payload = _chat_completion(
                api_key=api_key,
                model=f"gpt://{folder_id}/{model_name}",
                base_url=OPENAI_COMPATIBLE_URL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                temperature=temperature,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_format=response_format,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            choice = payload["choices"][0]
            raw_text = _strip_fences(_message_text(choice.get("message") or {}))
            if choice.get("finish_reason") == "content_filter" or is_refusal(raw_text):
                raise YandexContentFilterError(raw_text[:120] or "content_filter")
            return response_model.model_validate(_coerce_shape(raw_text, response_model))
        except YandexContentFilterError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                f"Запасная модель {model_name} не ответила (попытка {attempt} из {attempts}): "
                f"{exc}; начало ответа: {raw_text[:200]!r}"
            )
        if attempt < attempts:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise last_error if last_error else RuntimeError(f"Модель {model_name} не ответила")


def chunk_text(text: str, *, max_chars: int, overlap: int = 200) -> list[str]:
    """Перенесена в `app/services/ai_client.py` (не зависит от провайдера); здесь — для
    прежних импортов."""

    from app.services.ai_client import chunk_text as _chunk_text

    return _chunk_text(text, max_chars=max_chars, overlap=overlap)


# Модель эмбеддингов Yandex AI Studio. `doc` — вариант для индексируемых документов
# (в отличие от `query` для поисковых запросов): у нас обе стороны сравнения — тексты
# тендеров, то есть документы, и смешивать их с query-векторами нельзя.
EMBEDDINGS_MODEL = "doc"
# Сколько текста тендера уходит в вектор. Модель обрезает вход сама, но резать осмысленно
# лучше на нашей стороне — иначе в вектор попадёт хвост извещения вместо предмета закупки.
EMBEDDING_MAX_CHARS = 8000


def embed_text(db: Session, text: str, *, model_name: str = EMBEDDINGS_MODEL) -> tuple[list[float], str]:
    """Вектор текста через Yandex AI Studio Embeddings (раздел 5.5.1 ТЗ).

    Это ДРУГОЙ вызов API, не генерация текста через YandexGPT: отдельная модель, отдельный
    эндпоинт, свой формат ответа. Путать их нельзя — в ТЗ это оговорено отдельно, потому
    что оба вызова идут через один и тот же SDK и различаются одной строкой.

    Возвращает вектор и версию модели: векторы разных версий несравнимы, и без пометки
    старые строки после смены модели молча портили бы выдачу «похожих».
    """

    from yandex_ai_studio_sdk import AIStudio

    api_key, folder_id = get_credentials(db)
    sdk = AIStudio(folder_id=folder_id, auth=api_key)
    model = sdk.models.text_embeddings(model_name)

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            result = model.run(text[:EMBEDDING_MAX_CHARS], timeout=DEFAULT_TIMEOUT_SECONDS)
            return list(result.embedding), f"{model_name}:{model.version}"
        except Exception as exc:  # noqa: BLE001 - сетевые сбои и квоты лечатся повтором
            last_error = exc
            logger.warning(
                f"Не удалось получить эмбеддинг (попытка {attempt} из {RETRY_ATTEMPTS}): {exc}"
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise last_error if last_error else RuntimeError("Эмбеддинг не получен")
