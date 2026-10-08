import uuid

import httpx
import pytest

from app.db.session import SessionLocal
from app.models.source import Source
from app.services.availability_service import ping_source


@pytest.fixture(autouse=True)
def _no_ping_retry_pause(monkeypatch):
    from app.services import availability_service

    monkeypatch.setattr(availability_service, "PING_RETRY_PAUSE_SECONDS", 0)


class _FakeStreamResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _make_source(db) -> Source:
    source = Source(
        key=f"pingtest_{uuid.uuid4().hex[:8]}",
        name="Тестовая площадка",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def test_ping_source_marks_available_on_2xx(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "stream", lambda self, method, url: _FakeStreamResponse(200)
    )

    db = SessionLocal()
    try:
        source = _make_source(db)
        ping_source(db, source)
        assert source.availability_status == "available"
        assert source.availability_error is None
        assert source.availability_checked_at is not None
    finally:
        db.close()


def test_ping_source_marks_unavailable_on_5xx(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "stream", lambda self, method, url: _FakeStreamResponse(503)
    )

    db = SessionLocal()
    try:
        source = _make_source(db)
        ping_source(db, source)
        assert source.availability_status == "unavailable"
        assert source.availability_error == "HTTP 503"
    finally:
        db.close()


def test_ping_source_marks_unavailable_on_connection_error(monkeypatch):
    def _raise(self, method, url):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.Client, "stream", _raise)

    db = SessionLocal()
    try:
        source = _make_source(db)
        ping_source(db, source)
        assert source.availability_status == "unavailable"
        assert "boom" in (source.availability_error or "")
    finally:
        db.close()


def test_ping_source_logs_only_on_state_change(monkeypatch):
    from sqlalchemy import func, select

    from app.models.log import Log

    db = SessionLocal()
    try:
        source = _make_source(db)

        monkeypatch.setattr(
            httpx.Client, "stream", lambda self, method, url: _FakeStreamResponse(200)
        )
        ping_source(db, source)
        ping_source(db, source)  # тот же результат — второй раз не должен писать в лог

        count = db.scalar(
            select(func.count())
            .select_from(Log)
            .where(Log.action == f"ping_source:{source.key}")
        )
        assert count == 1
    finally:
        db.close()


def test_ping_retries_before_marking_unavailable_and_treats_429_as_alive(monkeypatch):
    """Разовый таймаут — не недоступность (Госплан «мигал» 27 раз за два дня), а 429 — сайт
    жив, просто просит реже (waviot.ru на ботовый User-Agent)."""

    from app.services import availability_service

    monkeypatch.setattr(availability_service, "PING_RETRY_PAUSE_SECONDS", 0)
    answers = iter([httpx.ConnectTimeout("t"), 200])

    def _once(source):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(availability_service, "_ping_once", _once)
    db = SessionLocal()
    try:
        source = _make_source(db)
        availability_service.ping_source(db, source)
        assert source.availability_status == "available"

        monkeypatch.setattr(availability_service, "_ping_once", lambda source: 429)
        availability_service.ping_source(db, source)
        assert source.availability_status == "available"
    finally:
        db.close()


def test_ping_respects_interval_per_source_type():
    """Пинг раз в минуту каждого сайта загонял Росэлторг и Инкотекс в бан антиDDoS
    (журнал сервера 01–08.10.2026) — источник проверяется только по своему интервалу."""

    from datetime import datetime, timedelta, timezone

    from app.services.availability_service import is_ping_due

    now = datetime.now(timezone.utc)
    platform = Source(key="x", name="x", url="https://example.test", type="etp_federal_commercial")
    catalog = Source(key="y", name="y", url="https://example.test", type="manufacturer_site")

    assert is_ping_due(platform, now)  # ни разу не проверялся
    platform.availability_checked_at = now - timedelta(minutes=5)
    assert not is_ping_due(platform, now)
    platform.availability_checked_at = now - timedelta(minutes=15)
    assert is_ping_due(platform, now)

    catalog.availability_checked_at = now - timedelta(minutes=30)
    assert not is_ping_due(catalog, now)
    catalog.availability_checked_at = now - timedelta(hours=1)
    assert is_ping_due(catalog, now)
