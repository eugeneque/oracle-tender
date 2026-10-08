"""Расписание планировщика — по Москве независимо от пояса сервера (08.10.2026).

На сервере заказчика (UTC) опрос «в 09:00 и 14:00» шёл в 12:00 и 17:00 по Москве: cron-триггеры
APScheduler без явного пояса берут пояс машины, а не планировщика."""

import time
from datetime import datetime, timezone

from app.core.scheduler import CronTrigger


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
