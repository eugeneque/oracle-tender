"""Журнал ошибок обращений к моделям ИИ (14.10.2026).

Сбои моделей раньше были рассыпаны: попытки — только в консоли сервера (`logger.warning`),
статус попытки — строкой в «Детализации ИИ» с обрезанной до 300 символов ошибкой, итоговый
отказ — где-то в журнале вызывающего сервиса под его собственным компонентом (`compliance`,
`tender_analysis`, …) или нигде. Понять «почему DeepSeek всю ночь падал» можно было, только
сопоставив три места. Теперь всё, что связано с ошибками запросов к ИИ, пишется сюда:

- каждая неудачная попытка (сеть, таймаут, HTTP-ошибка, ответ не по схеме, отказ фильтра,
  исчерпанный лимит) — из `ai_usage_service.record_call`, куда клиенты моделей и так сообщают
  о каждой попытке; уровень WARNING, если будет повтор, ERROR — если повторять бессмысленно;
- итоговый отказ после всех повторов, «ключ не задан», «модель отключена», переключение на
  YandexGPT по исчерпанному лимиту — из `ai_client.run_structured`;
- эмбеддинги и проверки подключения на странице «Интеграции».

Записи идут в общую таблицу `logs` с компонентом `ai_models` — страница «Логирование»
показывает их отдельной вкладкой, а выгрузка в файл работает без доработок. Дополнительно
каждая запись уходит в отдельный файл сервера `logs/ai.log` (`core/logging.py`) — его удобно
смотреть `tail -f` при отладке промптов.

Запись — **отдельной сессией** на подключении вызывающего кода, как и учёт токенов: расчёт
матрицы откатывает свою транзакцию после сбоя, и ошибка, ради которой журнал заведён, иначе
пропала бы вместе с ним. Сбой самой записи никогда не роняет обращение к модели.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.job import BackgroundJob
from app.models.log import Log, LogLevel
from app.models.tender import Tender
from app.services.ai_context import current_job_id, current_user_id

COMPONENT = "ai_models"
# Текст ошибки провайдера бывает с телом ответа целиком (HTML страницы шлюза); в журнале
# достаточно начала — по нему видно причину, а строка не растягивается на экран.
MAX_ERROR_CHARS = 2000

# Подписи статусов попытки — те же, что в «Детализации ИИ».
STATUS_ACTIONS: dict[str, str] = {
    "invalid": "Ответ модели не по схеме",
    "error": "Сбой запроса к модели",
    "quota": "Исчерпан лимит провайдера",
    "filter": "Отказ фильтра модели",
}

ai_logger = logger.bind(ai=True)


def operation_label(operation: str | None) -> str | None:
    if not operation:
        return None
    # Импорт внутри: ai_usage_service сам импортирует этот модуль.
    from app.services.ai_usage_service import OPERATION_LABELS

    return OPERATION_LABELS.get(operation, operation)


def _provider_label(provider: str | None) -> str | None:
    if not provider:
        return None
    from app.services.ai_provider_service import provider_label

    return provider_label(provider)


def log_ai_event(
    db: Session | None,
    *,
    action: str,
    error: str | Exception | None = None,
    level: LogLevel = LogLevel.ERROR,
    result: str = "error",
    provider: str | None = None,
    model: str | None = None,
    operation: str | None = None,
    attempt: int | None = None,
    attempts: int | None = None,
    duration_s: float | None = None,
) -> None:
    """Пишет событие в журнал ИИ: модель, шаг разбора, закупка и задача — из контекста, чтобы
    по строке журнала было понятно, что именно не получилось и где смотреть."""

    parts: list[str] = []
    provider_text = _provider_label(provider)
    if provider_text or model:
        parts.append(
            "Модель: " + " · ".join(item for item in (provider_text, model) if item)
        )
    label = operation_label(operation)
    if label:
        parts.append(f"Шаг: {label}")
    if attempt is not None:
        parts.append(f"Попытка: {attempt}" + (f" из {attempts}" if attempts else ""))
    if duration_s is not None:
        parts.append(f"Длительность: {duration_s:.1f} с")

    error_text = str(error).strip() if error is not None else ""
    if isinstance(error, Exception) and not error_text:
        error_text = type(error).__name__
    if len(error_text) > MAX_ERROR_CHARS:
        error_text = error_text[:MAX_ERROR_CHARS] + "…"

    user_id = current_user_id()
    job_id = current_job_id()

    # Файл ai.log пишется всегда, даже без БД (модульные тесты клиентов) и при сбое записи.
    file_line = f"{action} | " + " | ".join(parts)
    if error_text:
        file_line += f" | {error_text}"
    ai_logger.log(level.value, file_line)

    if db is None:
        return
    # `logs.user_id` — внешний ключ на `users`, а пишем другим соединением: если вызывающая
    # транзакция держит строку пользователя (создала его или обновила и ещё не закоммитила),
    # вставка ждала бы её вечно — тот же поток, взаимной блокировки PostgreSQL не увидит.
    # Поэтому короткий lock_timeout, а при нём — запись без автора (у `ai_usage_records`
    # внешних ключей нет по той же причине).
    for with_user in (True, False):
        try:
            _write(db, parts, error_text, action, result, level, user_id if with_user else None, job_id)
            return
        except Exception as exc:  # noqa: BLE001 - журнал не должен ронять обращение к модели
            if not with_user or user_id is None:
                logger.warning(f"Не удалось записать ошибку ИИ в журнал ({action}): {exc}")
                return


def _write(db, parts, error_text, action, result, level, user_id, job_id) -> None:
    parts = list(parts)
    with Session(bind=db.get_bind(), join_transaction_mode="create_savepoint") as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET LOCAL lock_timeout = '2s'"))
        job = session.get(BackgroundJob, job_id) if job_id else None
        if job is not None:
            tender = session.get(Tender, job.tender_id) if job.tender_id else None
            if tender is not None:
                parts.append(f"Закупка: {tender.external_id}")
            parts.append(f"Задача: {job.kind} ({job.id})")
        details = "\n".join(parts)
        if error_text:
            details += ("\n" if details else "") + f"Ошибка: {error_text}"
        session.add(
            Log(
                level=level,
                component=COMPONENT,
                action=action[:255],
                result=result[:50],
                details=details or None,
                user_id=user_id,
            )
        )
        session.commit()
