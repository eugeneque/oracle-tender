"""Снимок данных для новой установки и актуализация при старте (28.09.2026).

Полная загрузка снимка проверяется на отдельной базе вручную (`snapshot prepare` на пустой
базе); здесь — то, что должно держаться всегда: снимок в репозитории не несёт секретов и
людей, его ревизия есть в цепочке миграций, порядок загрузки соблюдает внешние ключи, а
актуализация при старте не опрашивает всё заново на каждый перезапуск.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from alembic.script import ScriptDirectory

from app.seed import snapshot
from app.services import startup_refresh


def test_load_order_puts_parents_first():
    fks = [
        ("products", "manufacturer_id", "manufacturers"),
        ("products", "si_type_id", "si_types"),
        ("si_types", "manufacturer_id", "manufacturers"),
        ("similar_tenders", "tender_id", "tenders"),
        ("similar_tenders", "similar_tender_id", "tenders"),
        ("tenders", "assignee_id", "users"),  # таблицы нет в снимке — порядок не держит
    ]
    order = snapshot._load_order(
        ["similar_tenders", "products", "tenders", "si_types", "manufacturers"], fks
    )
    assert order.index("manufacturers") < order.index("si_types") < order.index("products")
    assert order.index("tenders") < order.index("similar_tenders")


def test_load_order_reports_cycles():
    with pytest.raises(RuntimeError):
        snapshot._load_order(["a", "b"], [("a", "b_id", "b"), ("b", "a_id", "a")])


@pytest.mark.skipif(not snapshot.SNAPSHOT_PATH.is_file(), reason="снимок ещё не снят")
def test_committed_snapshot_has_no_secrets_and_known_revision():
    manifest = snapshot.read_manifest()
    assert manifest is not None
    assert not set(manifest["tables"]) & snapshot.EXCLUDED_TABLES

    script = ScriptDirectory.from_config(snapshot._alembic_config())
    assert script.get_revision(manifest["revision"]) is not None

    with zipfile.ZipFile(snapshot.SNAPSHOT_PATH) as zf:
        names = set(zf.namelist())
        assert "users.csv" not in names
        for table, columns in snapshot.NULLED_COLUMNS.items():
            if f"{table}.csv" not in names:
                continue
            rows = csv.DictReader(io.TextIOWrapper(zf.open(f"{table}.csv"), encoding="utf-8"))
            for row in rows:
                for column in columns:
                    assert row[column] == "", f"{table}.{column} должен быть пустым в снимке"


def test_recent_interval():
    now = datetime.now(timezone.utc)
    assert startup_refresh._recent(now - timedelta(hours=1), 12)
    assert not startup_refresh._recent(now - timedelta(hours=13), 12)
    assert not startup_refresh._recent(None, 12)


def test_catalog_refresh_skips_while_run_in_progress(monkeypatch):
    from app.services import catalog_autofill

    monkeypatch.setattr(catalog_autofill, "current_run_started_at", lambda _db: datetime.now(timezone.utc))
    monkeypatch.setattr(
        catalog_autofill,
        "run_all_in_background",
        lambda **_: pytest.fail("опрос не должен ставиться второй раз"),
    )
    assert startup_refresh.refresh_catalog(12) is None


def test_startup_refresh_is_off_without_scheduler(monkeypatch):
    """В тестах планировщик выключен — и актуализация при старте тоже не идёт в сеть."""

    started = []
    monkeypatch.setattr(startup_refresh.threading, "Thread", lambda **kw: started.append(kw))
    startup_refresh.start()
    assert started == []
