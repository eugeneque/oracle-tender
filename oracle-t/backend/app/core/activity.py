"""Идущие плановые процессы — для центра уведомлений в шапке (14.10.2026).

Ручные запуски (разбор закупки, опрос площадок по кнопке, очередь справочника) живут в БД
и видны оттуда. А задания планировщика — плановый опрос площадок, обход сайтов
производителей, сверки — выполняются прямо в потоке APScheduler и следа в БД, кроме записей
журнала, не оставляют: пользователь не видел, что сейчас идёт опрос и почему новых закупок
ещё нет.

Почему в памяти процесса, а не таблицей: состояние нужно только пока процесс идёт, а после
перезапуска сервера задание планировщика и так не продолжается — в таблице оставались бы
вечные «идёт» с той же уборкой, что у `background_jobs`. Планировщик работает в одном
процессе с API (uvicorn без `--workers`), поэтому реестр виден всем запросам.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone


@dataclass(frozen=True)
class SystemActivity:
    key: str
    title: str
    started_at: datetime
    status: str = "running"  # running | success | error
    done: int | None = None
    total: int | None = None
    message: str | None = None
    finished_at: datetime | None = None


_lock = threading.Lock()
_active: dict[str, SystemActivity] = {}
# Итоги последних процессов: в центре уведомлений видно, чем закончился утренний опрос.
_recent: deque[SystemActivity] = deque(maxlen=20)


def start(key: str, title: str) -> None:
    with _lock:
        _active[key] = SystemActivity(key=key, title=title, started_at=datetime.now(timezone.utc))


def progress(key: str, *, done: int | None = None, total: int | None = None, message: str | None = None) -> None:
    """Ход процесса. Вне `start` (тест, ручной вызов того же кода) ничего не делает."""

    with _lock:
        current = _active.get(key)
        if current is None:
            return
        _active[key] = replace(
            current,
            done=done if done is not None else current.done,
            total=total if total is not None else current.total,
            message=message if message is not None else current.message,
        )


def finish(key: str, *, error: str | None = None) -> None:
    with _lock:
        current = _active.pop(key, None)
        if current is None:
            return
        _recent.appendleft(
            replace(
                current,
                status="error" if error else "success",
                message=error or current.message,
                finished_at=datetime.now(timezone.utc),
            )
        )


def snapshot() -> tuple[list[SystemActivity], list[SystemActivity]]:
    """Идущие и недавно закончившиеся — копии, чтобы читающий не видел правок на лету."""

    with _lock:
        return list(_active.values()), list(_recent)


def reset() -> None:
    """Для тестов."""

    with _lock:
        _active.clear()
        _recent.clear()
