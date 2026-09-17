"""Проверка доступности источников тендеров — раз в минуту (`app/core/scheduler.py`),
независимо от того, реализован ли для площадки адаптер: пользователю нужно видеть в
«Настройках», какие из 12 площадок отвечают прямо сейчас, чтобы осмысленно выбирать
следующую волну подключения (раздел 9 ТЗ, Этап 2 → Этап 12) и понимать разовые сбои вроде
технических работ у самой площадки, а не у системы."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify
from app.models.log import LogLevel
from app.models.source import AvailabilityStatus, Source, SourceType
from app.services.audit import log_action

PING_TIMEOUT_SECONDS = 10.0


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "Таймаут подключения"
    if isinstance(exc, httpx.ReadTimeout | httpx.WriteTimeout | httpx.PoolTimeout):
        return "Таймаут ответа"
    if isinstance(exc, httpx.ConnectError):
        return f"Ошибка соединения: {exc}"
    if isinstance(exc, httpx.HTTPError):
        return f"Сетевая ошибка: {exc}"
    return f"{type(exc).__name__}: {exc}"


def ping_source(db: Session, source: Source) -> None:
    """Проверяет один источник. Не поднимает исключение наружу — сбой пинга одной площадки
    не должен прерывать проверку остальных (тот же принцип изоляции, что и при опросе тендеров,
    раздел 5.1, 5.9 ТЗ)."""

    was_available = source.availability_status == AvailabilityStatus.AVAILABLE.value

    try:
        with httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=PING_TIMEOUT_SECONDS,
            verify=resolve_verify(source.url),
            follow_redirects=True,
        ) as client:
            # `stream` вместо обычного GET: нужен только код ответа, тело страницы не читаем —
            # часть площадок отдаёт под мегабайт HTML на одну загрузку, а пинг идёт раз в минуту
            # по 12 источникам сразу.
            with client.stream("GET", source.url) as response:
                status_code = response.status_code

        if status_code < 400:
            source.availability_status = AvailabilityStatus.AVAILABLE.value
            source.availability_error = None
        else:
            source.availability_status = AvailabilityStatus.UNAVAILABLE.value
            source.availability_error = f"HTTP {status_code}"
    except Exception as exc:  # noqa: BLE001 - см. докстринг функции
        source.availability_status = AvailabilityStatus.UNAVAILABLE.value
        source.availability_error = _describe_error(exc)

    source.availability_checked_at = datetime.now(timezone.utc)
    db.add(source)

    is_available_now = source.availability_status == AvailabilityStatus.AVAILABLE.value
    if was_available != is_available_now:
        # В лог пишем только смену состояния, а не каждый пинг — иначе при интервале в минуту
        # на 12 источников журнал быстро станет нечитаемым (раздел 5.9 ТЗ требует фиксировать
        # события, а частый неизменный статус событием не является).
        log_action(
            db,
            component="availability",
            action=f"ping_source:{source.key}",
            result="available" if is_available_now else "unavailable",
            level=LogLevel.INFO if is_available_now else LogLevel.WARNING,
            details=source.availability_error,
        )
    db.commit()


def ping_all_sources(db: Session) -> None:
    # Источник ручных заявок — не сайт: у него нет адреса, который можно было бы проверить.
    for source in db.scalars(select(Source).where(Source.type != SourceType.MANUAL.value)).all():
        ping_source(db, source)
