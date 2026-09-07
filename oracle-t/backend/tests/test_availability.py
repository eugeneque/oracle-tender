import uuid

import httpx

from app.db.session import SessionLocal
from app.models.source import Source
from app.services.availability_service import ping_source


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
