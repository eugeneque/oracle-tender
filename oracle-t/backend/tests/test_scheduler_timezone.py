"""Расписание планировщика — по Москве независимо от пояса сервера (08.10.2026).

На сервере заказчика (UTC) опрос «в 09:00 и 14:00» шёл в 12:00 и 17:00 по Москве: cron-триггеры
APScheduler без явного пояса берут пояс машины, а не планировщика."""

import time
from datetime import datetime, timezone

from app.core.scheduler import CronTrigger
from app.core.timezones import MOSCOW_TZ


def test_cron_trigger_fires_by_moscow_time_on_utc_host(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        trigger = CronTrigger(hour=9, minute=0)
        fire = trigger.get_next_fire_time(None, datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc))
        assert str(trigger.timezone) == "Europe/Moscow"
        assert fire.astimezone(timezone.utc) == datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)
    finally:
        monkeypatch.undo()
        time.tzset()


def _fire_times_msk(scheduler, job_prefix: str) -> list[str]:
    after = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)
    times = []
    for job in scheduler.get_jobs():
        if job.id.startswith(job_prefix):
            fire = job.trigger.get_next_fire_time(None, after)
            times.append(fire.astimezone(MOSCOW_TZ).strftime("%H:%M"))
    return sorted(times)


def test_platforms_and_manufacturer_sites_are_polled_at_6_and_20_msk(monkeypatch):
    """Решение заказчика 13.10.2026: площадки и сайты производителей (автозаполнение
    каталога) опрашиваются в 06:00 и 20:00 по Москве — по умолчанию, без правки .env."""

    from apscheduler.schedulers.background import BackgroundScheduler

    from app.core import scheduler as scheduler_module
    from app.core.config import Settings

    settings = Settings(_env_file=None, scheduler_enabled=True, catalog_autofill_enabled=True)
    assert (settings.scheduler_morning_time, settings.scheduler_afternoon_time) == ("06:00", "20:00")
    monkeypatch.setattr(scheduler_module, "get_settings", lambda: settings)
    # Без запуска потоков: задачи остаются в списке ожидающих, их триггеры проверяем сами.
    monkeypatch.setattr(BackgroundScheduler, "start", lambda self, *args, **kwargs: None)
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        scheduler = scheduler_module.start_scheduler()
        assert _fire_times_msk(scheduler, "poll_sources") == ["06:00", "20:00"]
        assert _fire_times_msk(scheduler, "catalog_autofill") == ["06:00", "20:00"]
    finally:
        scheduler_module._scheduler = None
        monkeypatch.undo()
        time.tzset()
