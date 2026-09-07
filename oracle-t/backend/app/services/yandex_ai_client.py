"""Клиент текстовой генерации Yandex AI Studio / YandexGPT (раздел 5.4, 6.2 ТЗ).

Учётные данные берутся из БД (`yandex_ai_studio_settings`), а не из `.env` — по запросу
заказчика ключ настраивается из админ-панели (см. `app/services/yandex_ai_service.py`).
Этот модуль — тонкая обёртка: он не знает, *что* извлекается (характеристики продукции,
требования тендера и т.д.), только *как* вызвать модель и получить структурированный ответ.
Конкретные промпты живут в вызывающих сервисах.
"""

from __future__ import annotations

import time
from typing import TypeVar

import pydantic
from loguru import logger
from sqlalchemy.orm import Session

from app.models.integration_setting import YandexAiStudioSettings
from app.services.yandex_ai_service import _SINGLETON_ID

# Модель по умолчанию. `yandexgpt` (Pro) — а не `yandexgpt-lite`: задачи раздела 5.4-5.5 ТЗ
# (извлечение требований, сопоставление характеристик) требуют аккуратности на длинных
# технических текстах, где lite заметно слабее. Настраивается на случай смены поколения
# модели без правки кода.
DEFAULT_MODEL = "yandexgpt"
DEFAULT_TIMEOUT_SECONDS = 120.0

# Повторные попытки при сбое обращения к модели (раздел 5.9 ТЗ). Сбои здесь двух видов:
# сетевые/квотные (лечатся повтором) и «модель вернула не тот JSON» (лечится тоже — при
# temperature=0 повтор чаще всего даёт валидный ответ, потому что обрыв по лимиту токенов
# зависит от того, насколько многословно модель начала отвечать).
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3.0

ResponseT = TypeVar("ResponseT", bound=pydantic.BaseModel)


class YandexAiNotConfiguredError(RuntimeError):
    """Ключ/Folder ID не заданы в админ-панели — вызывающий код должен сообщить об этом
    пользователю понятным текстом, а не падать 500-й ошибкой."""


def get_credentials(db: Session) -> tuple[str, str]:
    settings = db.get(YandexAiStudioSettings, _SINGLETON_ID)
    if settings is None or not settings.api_key or not settings.folder_id:
        raise YandexAiNotConfiguredError(
            "Подключение к Yandex AI Studio не настроено: заполните API-ключ и Folder ID "
            "в разделе «Настройки → Интеграции»."
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
        temperature=temperature, response_format=response_model
    )

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            result = model.run(
                [
                    {"role": "system", "text": system_prompt},
                    {"role": "user", "text": user_text},
                ],
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            raw_text = result[0].text
            return response_model.model_validate_json(raw_text)
        except pydantic.ValidationError as exc:
            # Модель вернула JSON, не подходящий под схему (бывает при обрыве по лимиту токенов).
            # Логируем начало ответа — этого достаточно для диагностики, но не засоряет лог
            # многокилобайтным текстом.
            last_error = exc
            logger.warning(
                f"YandexGPT вернул ответ, не соответствующий схеме {response_model.__name__} "
                f"(попытка {attempt} из {RETRY_ATTEMPTS}): {exc}; начало ответа: {raw_text[:300]!r}"
            )
        except Exception as exc:  # noqa: BLE001 - сетевые сбои и квоты тоже лечатся повтором
            last_error = exc
            logger.warning(
                f"Обращение к YandexGPT не удалось (попытка {attempt} из {RETRY_ATTEMPTS}): {exc}"
            )

        if attempt < RETRY_ATTEMPTS:
            # Линейная задержка, а не экспонента: три попытки с шагом в несколько секунд
            # укладываются в разумное ожидание фоновой задачи, а сбои квоты снимаются и так.
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    # Цикл всегда завершается либо возвратом ответа, либо сохранённой ошибкой.
    raise last_error if last_error else RuntimeError("Обращение к YandexGPT не выполнено")


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
