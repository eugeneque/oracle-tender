"""Состояние типа СИ для интерфейса: вид прибора и срок свидетельства об утверждении типа.

Оба признака вычисляются при каждом запросе, а не хранятся в `review_status`. Раньше
предупреждение о сроке записывалось текстом при ревалидации и дальше не пересчитывалось:
у 83197-21 висело «осталось 9 дн.» на следующий день после истечения. Кроме того, три разных
случая (неоднозначность реестра, «не электросчётчик», истёкшее свидетельство) сливались в
одну пометку «требует ручной проверки», и по ней нельзя было понять, что делать.

Подтверждение человеком (`verified_by_user`) на состояние свидетельства не влияет: оно
означает «код правильно сопоставлен с производителем», а срок — факт реестра. Тип выходит
из «истёкших» только когда ревалидация принесёт из ФГИС новый срок действия.
"""

from __future__ import annotations

from datetime import date

from app.adapters.fgis_matching import DeviceKind, classify_si_type
from app.models.manufacturer import SiType

# Насколько заранее предупреждать об истекающем свидетельстве об утверждении типа. Полгода —
# не круглое число ради красоты: цикл «заметили → подали заявку в Росстандарт → получили
# новое свидетельство» занимает месяцы, и предупреждение за неделю уже бесполезно.
EXPIRY_WARNING_DAYS = 180

APPROVAL_VALID = "valid"
APPROVAL_EXPIRING = "expiring"
APPROVAL_EXPIRED = "expired"
APPROVAL_INACTIVE = "inactive"
# Срок в реестре не указан — бессрочные и старые типы; для закупки это не препятствие.
APPROVAL_UNKNOWN = "unknown"


def approval_state(si_type: SiType, today: date | None = None) -> tuple[str, int | None]:
    """Состояние свидетельства и число дней до окончания срока (отрицательное — истекло)."""

    today = today or date.today()
    days_left = (si_type.valid_to - today).days if si_type.valid_to else None
    if si_type.is_actual is False:
        return APPROVAL_INACTIVE, days_left
    if days_left is None:
        return APPROVAL_UNKNOWN, None
    if days_left < 0:
        return APPROVAL_EXPIRED, days_left
    if days_left <= EXPIRY_WARNING_DAYS:
        return APPROVAL_EXPIRING, days_left
    return APPROVAL_VALID, days_left


def is_electricity_meter(si_type: SiType) -> bool:
    """Тот же признак, по которому тип допускается к привязке моделей
    (`si_type_linking`): всё остальное — теплосчётчики, вода, газ, УСПД."""


    if not si_type.type_name and not si_type.notation:
        # Ручной ввод и CSV-импорт приходят без наименования типа: судить о виде не по чему,
        # а человек заводил код именно как электросчётчик.
        return True
    return classify_si_type(si_type.type_name, si_type.notation) is DeviceKind.ELECTRICITY_METER
