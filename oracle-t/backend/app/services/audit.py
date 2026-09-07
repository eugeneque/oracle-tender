import uuid

from sqlalchemy.orm import Session

from app.models.log import Log, LogLevel


def log_action(
    db: Session,
    *,
    component: str,
    action: str,
    result: str,
    level: LogLevel = LogLevel.INFO,
    details: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Log:
    """Записывает событие в журнал (раздел 5.9 ТЗ). Не коммитит транзакцию —
    вызывающий код должен сохранить изменения вместе с бизнес-операцией, чтобы
    запись в лог и сама операция были атомарны."""

    entry = Log(
        level=level,
        component=component,
        action=action,
        result=result,
        details=details,
        user_id=user_id,
    )
    db.add(entry)
    db.flush()
    return entry
