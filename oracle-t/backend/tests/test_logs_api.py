"""Тесты чтения журнала операций (раздел 5.9 ТЗ) — раздел «Логирование» в интерфейсе.

Проверяется то, из-за чего журналом перестают пользоваться: фильтр, который молча не
применился (человек видит не то, что просил, и делает неверный вывод о работе системы),
и доступ — журнал открыт всем пользователям, но не анонимам.
"""

from __future__ import annotations

import uuid

from app.models.log import Log, LogLevel
from app.services import log_service


def _entry(db, *, component: str, action: str, level: LogLevel, details: str | None = None) -> Log:
    entry = Log(
        component=component,
        action=action,
        result="success",
        level=level,
        details=details,
    )
    db.add(entry)
    db.flush()
    return entry


def test_filter_by_level_returns_only_requested(db_session):
    marker = uuid.uuid4().hex[:8]
    _entry(db_session, component=f"c_{marker}", action="ok", level=LogLevel.INFO)
    _entry(db_session, component=f"c_{marker}", action="broken", level=LogLevel.ERROR)

    page = log_service.get_logs(db_session, component=f"c_{marker}", levels=["ERROR"])

    assert page.total == 1
    assert page.items[0].action == "broken"
    assert page.items[0].level == "ERROR"


def test_search_matches_details_and_action(db_session):
    marker = uuid.uuid4().hex[:8]
    _entry(
        db_session,
        component=f"c_{marker}",
        action="poll_source:eis",
        level=LogLevel.WARNING,
        details="Таймаут подключения",
    )
    _entry(db_session, component=f"c_{marker}", action="export_tenders", level=LogLevel.INFO)

    by_details = log_service.get_logs(db_session, component=f"c_{marker}", search="таймаут")
    by_action = log_service.get_logs(db_session, component=f"c_{marker}", search="export")

    assert [item.action for item in by_details.items] == ["poll_source:eis"]
    assert [item.action for item in by_action.items] == ["export_tenders"]


def test_newest_entries_come_first(db_session):
    marker = uuid.uuid4().hex[:8]
    _entry(db_session, component=f"c_{marker}", action="первое", level=LogLevel.INFO)
    _entry(db_session, component=f"c_{marker}", action="второе", level=LogLevel.INFO)

    page = log_service.get_logs(db_session, component=f"c_{marker}")

    assert [item.action for item in page.items] == ["второе", "первое"]


def test_total_counts_all_matches_not_just_page(db_session):
    """`total` считает всю выборку фильтра, а не строки на экране: иначе «5 записей»
    при лимите 5 выглядело бы так, будто больше ничего и не было."""

    marker = uuid.uuid4().hex[:8]
    for index in range(7):
        _entry(db_session, component=f"c_{marker}", action=f"строка {index}", level=LogLevel.INFO)

    page = log_service.get_logs(db_session, component=f"c_{marker}", limit=3)

    assert len(page.items) == 3
    assert page.total == 7


def test_user_name_is_resolved(db_session, admin_user):
    marker = uuid.uuid4().hex[:8]
    entry = _entry(db_session, component=f"c_{marker}", action="login", level=LogLevel.INFO)
    entry.user_id = admin_user.id
    db_session.flush()

    page = log_service.get_logs(db_session, component=f"c_{marker}")

    assert page.items[0].user_name == admin_user.full_name


def test_logs_endpoint_requires_auth(client):
    assert client.get("/logs").status_code == 401


def test_logs_endpoint_available_to_regular_user(client, admin_token):
    """Журнал системы читают все пользователи, а не только администратор: без него
    непонятно, почему у тендера нет требований или почему площадка не опрашивалась."""

    created = client.post(
        "/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": f"log_reader_{uuid.uuid4().hex[:6]}",
            "password": "LogReader123!",
            "full_name": "Читатель журнала",
            "role": "user",
        },
    )
    assert created.status_code in (200, 201), created.text

    token = client.post(
        "/auth/login",
        json={"username": created.json()["username"], "password": "LogReader123!"},
    ).json()["access_token"]

    response = client.get("/logs?limit=5", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "items" in response.json()

    facets = client.get("/logs/facets", headers={"Authorization": f"Bearer {token}"})
    assert facets.status_code == 200
    assert "INFO" in facets.json()["levels"]


def test_purge_removes_only_entries_older_than_retention(db_session):
    """Хранение журнала — 6 месяцев (раздел 5.9 ТЗ). Свежие записи уборка трогать не должна:
    ошибка в границе стоила бы всей истории работы системы."""

    from datetime import datetime, timedelta, timezone

    marker = uuid.uuid4().hex[:8]
    old = _entry(db_session, component=f"c_{marker}", action="старое", level=LogLevel.INFO)
    old.timestamp = datetime.now(timezone.utc) - timedelta(days=200)
    fresh = _entry(db_session, component=f"c_{marker}", action="свежее", level=LogLevel.INFO)
    db_session.flush()

    log_service.purge_old_entries(db_session, retention_days=180)

    remaining = log_service.get_logs(db_session, component=f"c_{marker}")
    assert [item.action for item in remaining.items] == ["свежее"]
    assert remaining.items[0].id == fresh.id
    assert old.id not in {item.id for item in remaining.items}
