"""Чтение журнала операций (раздел 5.9 ТЗ) для раздела «Логирование» в интерфейсе.

Журнал показывается всем пользователям, а не только администратору: по ТЗ логируются
операции системы (опрос источников, разбор документов, ИИ-анализ, экспорт), и обычному
пользователю нужно понимать, почему у тендера нет требований или почему площадка не
опрашивалась. Персональные данные в журнал не пишутся — только имя действующего
пользователя, которое и так видно в истории тендера.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


# --- выгрузка журнала в файл (28.09.2026) ------------------------------------------------
#
# На экране журнал показывает последние 200 строк — для разбора инцидента («что происходило
# ночью с опросом ЕИС») этого мало, а переслать разработчику скриншот терминала неудобно.
# Выгрузка отдаёт весь период целиком обычным текстом или Markdown: первый читается в любом
# редакторе и грепается, второй — таблицей в мессенджере, GitLab или Obsidian.

EXPORT_FORMATS = ("txt", "md")
# Потолок строк в файле: неделя журнала в обычном режиме — единицы тысяч записей, но при
# зацикленной ошибке их бывают сотни тысяч, и такой файл уже никто не прочитает.
EXPORT_MAX_ROWS = 100_000
# Время в файле — московское: по нему работают пользователи, а в БД оно хранится в UTC.
EXPORT_TZ = ZoneInfo("Europe/Moscow")

_PERIOD_LABELS = {1: "последний час", 24: "последние сутки", 24 * 7: "последняя неделя"}
_LEVEL_ORDER = ("INFO", "WARNING", "ERROR", "CRITICAL")


def _period_label(hours: int) -> str:
    return _PERIOD_LABELS.get(hours, f"последние {hours} ч")


def _level_of(entry: Log) -> str:
    return entry.level.value if hasattr(entry.level, "value") else str(entry.level)


def _md_cell(value: str | None) -> str:
    """Ячейка Markdown-таблицы: вертикальная черта и перевод строки ломают таблицу."""

    if not value:
        return ""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def export_logs(
    db: Session,
    *,
    hours: int,
    fmt: str,
    actor: User,
    levels: list[str] | None = None,
    component: str | None = None,
    search: str | None = None,
) -> tuple[bytes, str, int]:
    """Журнал за последние `hours` часов файлом `.txt` или `.md` с теми же фильтрами, что на
    экране. Возвращает содержимое, имя файла и число записей. Строки — от старых к новым,
    как читают лог."""

    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Неизвестный формат выгрузки: {fmt}")

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    filter_args = {"levels": levels, "component": component, "search": search, "since": since}

    total = db.execute(_apply_filters(select(func.count(Log.id)), **filter_args)).scalar_one()
    rows = db.execute(
        _apply_filters(
            select(Log, User.full_name).outerjoin(User, User.id == Log.user_id), **filter_args
        )
        # Потолок срезает самые старые записи, а не свежие: при разборе важнее конец периода.
        .order_by(Log.timestamp.desc())
        .limit(EXPORT_MAX_ROWS)
    ).all()
    rows = list(reversed(rows))

    def local(value: datetime) -> str:
        return value.astimezone(EXPORT_TZ).strftime("%d.%m.%Y %H:%M:%S")

    counts = dict.fromkeys(_LEVEL_ORDER, 0)
    for entry, _ in rows:
        counts[_level_of(entry)] = counts.get(_level_of(entry), 0) + 1

    filters: list[str] = []
    if levels:
        filters.append(f"уровни: {', '.join(levels)}")
    if component:
        filters.append(f"компонент: {component}")
    if search and search.strip():
        filters.append(f"поиск: «{search.strip()}»")

    rows_note = str(len(rows))
    if total > len(rows):
        rows_note += f" из {total} (самые старые не вошли, потолок {EXPORT_MAX_ROWS})"
    header = [
        ("Период", f"{local(since)} — {local(now)} МСК ({_period_label(hours)})"),
        ("Фильтр", "; ".join(filters) if filters else "нет — все записи"),
        ("Записей", rows_note),
        ("По уровням", ", ".join(f"{level} {count}" for level, count in counts.items())),
        ("Выгрузил", f"{actor.full_name or actor.username}, {local(now)} МСК"),
    ]

    lines: list[str] = []
    if fmt == "md":
        lines.append("# Журнал операций Sova")
        lines.append("")
        lines.extend(f"- **{name}:** {value}" for name, value in header)
        lines.append("")
        if rows:
            lines.append(
                "| Время (МСК) | Уровень | Компонент | Действие | Результат | Детали | Пользователь |"
            )
            lines.append("|---|---|---|---|---|---|---|")
            for entry, user_name in rows:
                level = _level_of(entry)
                # Ошибки выделены жирным — в длинной таблице их ищут глазами первыми.
                shown_level = f"**{level}**" if level in ("ERROR", "CRITICAL") else level
                lines.append(
                    f"| {local(entry.timestamp)} | {shown_level} | {_md_cell(entry.component)} | "
                    f"{_md_cell(entry.action)} | {_md_cell(entry.result)} | "
                    f"{_md_cell(entry.details)} | {_md_cell(user_name)} |"
                )
        else:
            lines.append("_Записей за период нет._")
    else:
        lines.append("Журнал операций Sova")
        lines.extend(f"{name}: {value}" for name, value in header)
        lines.append("=" * 100)
        # Классический формат лога: одна запись — одна строка, поля через « | », чтобы файл
        # разбирался grep/awk. Многострочные детали — с отступом под своей записью.
        for entry, user_name in rows:
            line = (
                f"{local(entry.timestamp)} | {_level_of(entry):<8} | {entry.component} | "
                f"{entry.action} → {entry.result}"
            )
            if entry.details:
                line += f" | {entry.details}"
            if user_name:
                line += f" | [{user_name}]"
            lines.append(line.replace("\r", "").replace("\n", "\n    "))
        if not rows:
            lines.append("Записей за период нет.")

    content = ("\n".join(lines) + "\n").encode("utf-8")
    file_name = f"sova-log_{now.astimezone(EXPORT_TZ):%Y-%m-%d_%H%M}_{hours}h.{fmt}"

    log_action(
        db,
        component="logs",
        action="export_logs",
        result="success",
        level=LogLevel.INFO,
        details=f"Выгрузка журнала в .{fmt}: {_period_label(hours)}, записей {len(rows)}",
        user_id=actor.id,
    )
    db.commit()
    return content, file_name, len(rows)


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
