"""Чтение журнала операций (раздел 5.9 ТЗ) для раздела «Логирование» в интерфейсе.

Журнал показывается всем пользователям, а не только администратору: по ТЗ логируются
операции системы (опрос источников, разбор документов, ИИ-анализ, экспорт), и обычному
пользователю нужно понимать, почему у тендера нет требований или почему площадка не
опрашивалась. Персональные данные в журнал не пишутся — только имя действующего
пользователя, которое и так видно в истории тендера.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.log import Log, LogLevel
from app.models.notification import Notification
from app.models.user import User
from app.schemas.log import LogEntryOut, LogFacets, LogPage
from app.services.audit import log_action

MAX_LIMIT = 500


def _apply_filters(
    query: Select,
    *,
    levels: list[str] | None,
    component: str | None,
    search: str | None,
    since: datetime | None,
) -> Select:
    if levels:
        query = query.where(Log.level.in_(levels))
    if component:
        query = query.where(Log.component == component)
    if since is not None:
        query = query.where(Log.timestamp >= since)
    if search:
        pattern = f"%{search.strip()}%"
        # Поиск идёт по трём текстовым полям сразу: пользователь ищет «ЕИС» или «таймаут»,
        # не зная, в какой колонке это лежит.
        query = query.where(
            or_(
                Log.action.ilike(pattern),
                Log.details.ilike(pattern),
                Log.component.ilike(pattern),
            )
        )
    return query


def get_logs(
    db: Session,
    *,
    levels: list[str] | None = None,
    component: str | None = None,
    search: str | None = None,
    since: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> LogPage:
    limit = max(1, min(limit, MAX_LIMIT))

    total = db.execute(
        _apply_filters(
            select(func.count(Log.id)),
            levels=levels,
            component=component,
            search=search,
            since=since,
        )
    ).scalar_one()

    # Имя пользователя подтягивается сразу (LEFT JOIN): иначе на каждую из двухсот строк
    # ушёл бы отдельный запрос за автором.
    query = _apply_filters(
        select(Log, User.full_name).outerjoin(User, User.id == Log.user_id),
        levels=levels,
        component=component,
        search=search,
        since=since,
    )
    rows = db.execute(
        query.order_by(Log.timestamp.desc()).limit(limit).offset(max(0, offset))
    ).all()

    items = [
        LogEntryOut(
            id=entry.id,
            timestamp=entry.timestamp,
            level=entry.level.value if hasattr(entry.level, "value") else str(entry.level),
            component=entry.component,
            action=entry.action,
            result=entry.result,
            details=entry.details,
            user_name=user_name,
        )
        for entry, user_name in rows
    ]
    return LogPage(items=items, total=total)


def get_facets(db: Session) -> LogFacets:
    components = (
        db.execute(select(Log.component).distinct().order_by(Log.component)).scalars().all()
    )
    return LogFacets(
        components=list(components),
        levels=["INFO", "WARNING", "ERROR", "CRITICAL"],
    )


# Срок хранения журнала — 6 месяцев (раздел 5.9 ТЗ). Ротация файлового лога у loguru уже
# настроена на те же 180 дней (`app/core/logging.py`), а таблица `logs` росла без ограничений.
RETENTION_DAYS = 180


def purge_old_entries(db: Session, *, retention_days: int = RETENTION_DAYS) -> int:
    """Удаляет записи журнала и уведомлений старше срока хранения. Возвращает число
    удалённых строк журнала.

    Удаление, а не архивирование: ТЗ требует хранить шесть месяцев, а не «шесть месяцев
    под рукой и остальное где-то ещё» — второе хранилище пришлось бы кому-то обслуживать.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    removed_logs = db.execute(delete(Log).where(Log.timestamp < cutoff)).rowcount or 0
    removed_notifications = (
        db.execute(delete(Notification).where(Notification.created_at < cutoff)).rowcount or 0
    )

    if removed_logs or removed_notifications:
        # Сама уборка тоже попадает в журнал: молча исчезнувшие записи выглядели бы как
        # потеря данных.
        log_action(
            db,
            component="logs",
            action="purge_old_entries",
            result="success",
            level=LogLevel.INFO,
            details=(
                f"Удалено записей журнала: {removed_logs}, "
                f"уведомлений: {removed_notifications} (старше {retention_days} дней)"
            ),
        )
    db.commit()
    return removed_logs
