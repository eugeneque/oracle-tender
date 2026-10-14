"""Центр уведомлений в шапке: запуски пользователя с итогами и общие процессы (14.10.2026)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import activity
from app.models.catalog_queue import CatalogLookupTask
from app.models.job import BackgroundJob, JobKind
from app.models.user import User
from app.services import activity_service


def _user(db, name: str) -> User:
    user = User(username=f"act_{uuid.uuid4().hex[:6]}", password_hash="x", full_name=name)
    db.add(user)
    db.flush()
    return user


@pytest.fixture(autouse=True)
def _clean_registry():
    activity.reset()
    yield
    activity.reset()


def test_own_poll_with_progress_and_foreign_poll_as_system(db_session):
    me, other = _user(db_session, "Я"), _user(db_session, "Коллега")
    db_session.add_all(
        [
            BackgroundJob(
                kind=JobKind.SOURCES_POLL.value,
                status="running",
                created_by_id=me.id,
                payload={"source_keys": ["a", "b", "c", "d"], "results": [{}]},
            ),
            BackgroundJob(
                kind=JobKind.SOURCES_POLL.value,
                status="running",
                created_by_id=other.id,
                payload={"source_keys": ["a"]},
            ),
        ]
    )
    db_session.flush()

    items = activity_service.feed(db_session, me).items
    mine = [i for i in items if i.scope == "mine"]
    system = [i for i in items if i.scope == "system"]
    assert len(mine) == 1 and mine[0].progress == pytest.approx(0.25)
    assert len(system) == 1 and system[0].owner_name == "Коллега"


def test_catalog_tasks_of_one_click_are_one_line(db_session):
    me = _user(db_session, "Я")
    now = datetime.now(timezone.utc)
    for index, status in enumerate(["success", "error", "running", "queued"]):
        db_session.add(
            CatalogLookupTask(
                adapter_key="catalog_autofill",
                reason="manual",
                status=status,
                model_name=f"Производитель {index}",
                created_by_id=me.id,
                created_at=now + timedelta(seconds=index),
                finished_at=now if status in ("success", "error") else None,
                details={},
            )
        )
    db_session.flush()

    mine = [i for i in activity_service.feed(db_session, me).items if i.scope == "mine"]
    assert len(mine) == 1
    assert mine[0].title == "Опрос каталога"
    assert mine[0].status == "running"
    assert mine[0].progress == pytest.approx(0.5)


def test_scheduler_process_in_registry(db_session):
    me = _user(db_session, "Я")
    activity.start("poll_sources", "Плановый опрос площадок")
    activity.progress("poll_sources", done=3, total=12, message="Опрос: ЕИС")

    system = [i for i in activity_service.feed(db_session, me).items if i.kind == "poll_sources"]
    assert system[0].status == "running"
    assert system[0].progress == pytest.approx(0.25)

    activity.finish("poll_sources")
    system = [i for i in activity_service.feed(db_session, me).items if i.kind == "poll_sources"]
    assert system[0].status == "success" and system[0].finished_at is not None


def test_activity_endpoint(client, admin_token):
    response = client.get("/activity", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200, response.text
    assert "items" in response.json()
