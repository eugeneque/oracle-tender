"""Плановые прогоны видны в журнале и не теряются молча (14.10.2026).

С 13.10 15:00 до ручного запуска 14.10 12:01 в журнале не было ни одного опроса площадок, и
по журналу нельзя было понять, «не было по расписанию» это или «пропущено/упало»: APScheduler
по умолчанию даёт запуску секунду на опоздание и о пропуске пишет только в stdout."""

from datetime import datetime

from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent
from sqlalchemy import select

from app.core import scheduler as scheduler_module
from app.core.timezones import MOSCOW_TZ
from app.models.log import Log, LogLevel
from app.services.tender_service import SourcePollResult


def _journal_into(monkeypatch, db_session) -> None:
    """Записи планировщика — в тестовую сессию: `_journal` открывает свою и закрывает её."""

    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)


def _entries(db_session, action: str) -> list[Log]:
    return list(db_session.scalars(select(Log).where(Log.action == action).order_by(Log.timestamp)))


def test_jobs_get_misfire_grace_coalesce_and_single_instance(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.core.config import Settings

    settings = Settings(_env_file=None, scheduler_enabled=True, catalog_autofill_enabled=True)
    monkeypatch.setattr(scheduler_module, "get_settings", lambda: settings)
    # Настоящий старт, но на паузе: умолчания `job_defaults` APScheduler подставляет в задания
    # только при старте, а выполнять их в тесте незачем.
    original_start = BackgroundScheduler.start
    monkeypatch.setattr(
        BackgroundScheduler, "start", lambda self, *a, **k: original_start(self, paused=True)
    )
    scheduler = scheduler_module.start_scheduler()
    try:
        jobs = {job.id: job for job in scheduler.get_jobs()}
        for job_id in ("poll_sources_morning", "poll_sources_afternoon", "catalog_autofill"):
            job = jobs[job_id]
            assert job.misfire_grace_time == scheduler_module.MISFIRE_GRACE_SECONDS >= 3600
            assert job.coalesce is True
            assert job.max_instances == 1
    finally:
        scheduler.shutdown(wait=False)
        scheduler_module._scheduler = None


def test_missed_run_is_written_to_journal_as_warning(monkeypatch, db_session):
    _journal_into(monkeypatch, db_session)
    event = JobExecutionEvent(
        EVENT_JOB_MISSED,
        "poll_sources_morning",
        "default",
        datetime(2026, 10, 14, 6, 0, tzinfo=MOSCOW_TZ),
    )

    scheduler_module._on_job_skipped(event)

    [entry] = _entries(db_session, "job_missed:poll_sources_morning")
    assert entry.level == LogLevel.WARNING
    assert entry.component == "scheduler"
    assert "Плановый опрос площадок на 14.10.2026 06:00 МСК пропущен" in entry.details


def test_scheduled_poll_writes_start_and_finish(monkeypatch, db_session):
    _journal_into(monkeypatch, db_session)
    monkeypatch.setattr(scheduler_module, "list_pollable_sources", lambda db: [object(), object()])
    monkeypatch.setattr(
        scheduler_module,
        "poll_all_active_sources",
        lambda db: [
            SourcePollResult("eis", 10, 3, 7, 0),
            SourcePollResult("roseltorg", 5, 2, 3, 0),
        ],
    )
    monkeypatch.setattr(scheduler_module, "check_pending_ai_relevance", lambda db: None)
    monkeypatch.setattr(scheduler_module, "_run_gaps_job", lambda: None)

    scheduler_module._run_poll_job()

    started, finished = _entries(db_session, "scheduled_poll")
    assert (started.result, started.details) == ("started", "Плановый опрос запущен: площадок 2")
    assert finished.result == "success"
    assert finished.details.startswith("Плановый опрос завершён: площадок 2, создано 5")
