"""Время в системе: хранение в UTC, отображение в МСК (раздел 8 ТЗ).

Правило простое, но нарушается легко: `datetime.now()` без аргумента возвращает время по
часам машины. На сервере, настроенном в UTC (обычная конфигурация в Docker), такая строка
уезжает на три часа назад — и «Дата формирования отчёта» в Excel показывает не то время,
когда отчёт делали. Ошибка тихая: цифра выглядит правдоподобно и замечается только когда
кто-то сверяет отчёт с журналом.

Поэтому одно место, откуда берётся «сейчас для показа человеку», и одно — для хранения.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def now_utc() -> datetime:
    """«Сейчас» для хранения и вычислений. Всегда с указанием пояса."""

    return datetime.now(timezone.utc)


def now_msk() -> datetime:
    """«Сейчас» для показа человеку — независимо от того, как настроены часы сервера."""

    return datetime.now(MOSCOW_TZ)


def to_msk(value: datetime | None) -> datetime | None:
    """Переводит момент времени в МСК для отображения.

    Наивный `datetime` (без пояса) считается временем UTC: так его отдаёт база для колонок,
    заведённых без `timezone=True`, и трактовать его как местное время сервера значило бы
    получать разный результат на разных машинах.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW_TZ)
