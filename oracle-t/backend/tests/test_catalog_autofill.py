"""Автозаполнение каталога: сквозной опрос производителя (замечание заказчика 28.09.2026).

Внешние источники подменены — проверяется связка шагов: порядок, изоляция сбоя одного
шага, итог задачи для значка в списке каталога и захват задачи одним потоком."""

from __future__ import annotations

import uuid

import pytest

from app.models.catalog_queue import CatalogQueueStatus
from app.core import dns_fallback
from app.models.manufacturer import Manufacturer, Product
from app.services import catalog_autofill, catalog_queue_service


@pytest.fixture(autouse=True)
def _registered():
    from app.services import catalog_learning

    catalog_autofill.register()
    catalog_learning.register()


@pytest.fixture()
def manufacturer(db_session) -> Manufacturer:
    item = Manufacturer(
        legal_name=f'ООО «Автозаполнение {uuid.uuid4().hex[:8]}»', brand_name="Авто", is_mirtek=False
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    db_session.add(Product(manufacturer_id=item.id, model_name="Счётчик АВТ-1", model_code="АВТ-1"))
    db_session.commit()
    return item


@pytest.fixture()
def steps(monkeypatch, admin_user):
    """Шаги прохода подменены записью вызовов; `fail` — какие шаги падают."""

    calls: list[str] = []
    fail: set[str] = set()

    def stub(key: str, message: str | None = "ок"):
        def run(db, manufacturer, actor):
            calls.append(key)
            if key in fail:
                raise RuntimeError(f"{key} недоступен")
            return message

        return run

    monkeypatch.setattr(catalog_autofill, "_search_si_types", stub("si_search"))
    # Сайта-профиля нет — шаг неприменим и в итог не попадает.
    monkeypatch.setattr(catalog_autofill, "_sync_site", stub("site", None))
    monkeypatch.setattr(catalog_autofill, "_registry", stub("registry"))
    link_calls = iter(("link", "relink"))
    monkeypatch.setattr(
        catalog_autofill, "_link", lambda db, m, a: stub(next(link_calls))(db, m, a)
    )
    return calls, fail


def test_all_steps_run_in_order(db_session, manufacturer, steps):
    calls, _ = steps
    task = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)

    counters = catalog_queue_service.process_queue(db_session, adapter_key=catalog_autofill.ADAPTER_KEY)

    assert counters["success"] == 1
    assert calls == ["si_search", "site", "link", "registry", "relink"]
    db_session.refresh(task)
    assert task.status == CatalogQueueStatus.SUCCESS.value
    assert [s["step"] for s in task.details["steps"]] == ["si_search", "link", "registry", "relink"]
    assert task.details["current_step"] is None


def test_failed_step_does_not_stop_the_rest(db_session, manufacturer, steps):
    """ФГИС недоступен — сайт, реестр и привязка всё равно выполняются, а итог
    помечается «требует проверки», а не молча «готово»."""

    calls, fail = steps
    fail.add("si_search")
    task = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)

    catalog_queue_service.process_queue(db_session, adapter_key=catalog_autofill.ADAPTER_KEY)

    assert calls == ["si_search", "site", "link", "registry", "relink"]
    db_session.refresh(task)
    assert task.status == CatalogQueueStatus.NEEDS_REVIEW.value
    assert task.details["failed_steps"] == ["si_search"]
    assert task.attempts == 1  # весь проход не повторяется из-за одного шага


def test_status_shows_latest_run_per_manufacturer(db_session, manufacturer, steps):
    catalog_autofill.enqueue(db_session, manufacturer, run_now=False)
    status = {row["manufacturer_id"]: row for row in catalog_autofill.status_by_manufacturer(db_session)}
    assert status[manufacturer.id]["status"] == CatalogQueueStatus.QUEUED.value

    catalog_queue_service.process_queue(db_session, adapter_key=catalog_autofill.ADAPTER_KEY)
    status = {row["manufacturer_id"]: row for row in catalog_autofill.status_by_manufacturer(db_session)}
    assert status[manufacturer.id]["status"] == CatalogQueueStatus.SUCCESS.value
    assert status[manufacturer.id]["finished_at"] is not None


def test_repeat_request_while_queued_is_not_duplicated(db_session, manufacturer, steps):
    first = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)
    second = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)
    assert first.id == second.id


def test_claimed_task_is_not_taken_twice(db_session, manufacturer, steps):
    """Задача, которую уже взял другой поток, пропускается — производитель не
    опрашивается дважды параллельно."""

    calls, _ = steps
    task = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)
    assert catalog_queue_service._claim(db_session, task) is True

    catalog_queue_service.process_queue(db_session, adapter_key=catalog_autofill.ADAPTER_KEY)

    assert calls == []


def test_scheduled_pickup_skips_long_autofill_runs(db_session, manufacturer, steps):
    calls, _ = steps
    catalog_autofill.enqueue(db_session, manufacturer, run_now=False)

    catalog_queue_service.process_queue(db_session, exclude_keys=(catalog_autofill.ADAPTER_KEY,))

    assert calls == []


def test_empty_catalog_is_not_reported_as_done(db_session, steps):
    """Все источники ответили, а моделей нет — не «готово» (жалоба 28.09.2026)."""

    empty = Manufacturer(legal_name=f'ООО «Пусто {uuid.uuid4().hex[:8]}»', is_mirtek=False)
    db_session.add(empty)
    db_session.commit()
    task = catalog_autofill.enqueue(db_session, empty, run_now=False)

    catalog_queue_service.process_queue(db_session, adapter_key=catalog_autofill.ADAPTER_KEY)

    db_session.refresh(task)
    assert task.status == CatalogQueueStatus.NEEDS_REVIEW.value
    assert task.details["empty"] is True
    status = {r["manufacturer_id"]: r for r in catalog_autofill.status_by_manufacturer(db_session)}
    assert status[empty.id]["empty"] is True


def test_unreachable_fgis_is_a_failed_step_not_zero_found(db_session, manufacturer, admin_user, monkeypatch):
    """ФГИС не ответил — шаг «коды СИ» падает, а не сообщает «найдено 0»."""

    from app.adapters import fgis

    def unreachable(self, url, *, params, what):
        self.errors.append(f"{what}: [Errno 8] nodename nor servname provided")
        return None

    monkeypatch.setattr(fgis.FgisAdapter, "_get_json", unreachable)
    with pytest.raises(RuntimeError, match="ФГИС не ответил"):
        catalog_autofill._search_si_types(db_session, manufacturer, admin_user)


def test_dns_fallback_is_used_only_when_resolution_fails(monkeypatch):
    import socket

    assert dns_fallback.parse("fgis.gost.ru=1.2.3.4, 5.6.7.8;x.ru=9.9.9.9") == {
        "fgis.gost.ru": ["1.2.3.4", "5.6.7.8"],
        "x.ru": ["9.9.9.9"],
    }

    def fake_resolve(host, port, *args):
        if host == "fgis.gost.ru":
            raise socket.gaierror(8, "nodename nor servname provided")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]

    monkeypatch.setattr(dns_fallback, "_original_getaddrinfo", fake_resolve)
    monkeypatch.setattr(dns_fallback, "_fallback", dns_fallback.parse("fgis.gost.ru=212.164.138.14"))
    assert dns_fallback._getaddrinfo("fgis.gost.ru", 443)[0][4] == ("212.164.138.14", 443)
    assert dns_fallback._getaddrinfo("ok.ru", 443)[0][4] == ("ok.ru", 443)
    with pytest.raises(socket.gaierror):
        monkeypatch.setattr(dns_fallback, "_fallback", {})
        dns_fallback._getaddrinfo("fgis.gost.ru", 443)


def test_run_resumes_after_restart_from_the_interrupted_step(db_session, manufacturer, steps):
    """Сервер перезапустился посреди опроса: пройденные шаги не повторяются (жалоба
    28.09.2026 — при частых перезапусках МИРТЕК опрашивался с начала и не заканчивал)."""

    calls, _ = steps
    task = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)
    # Так задачу оставляет `recover_interrupted_tasks`: снова в очереди, ход сохранён.
    task.details = {
        "current_step": "registry",
        "steps": [{"step": "si_search", "ok": True, "message": "найдено 3"}],
        "skipped_steps": ["site"],
    }
    db_session.commit()

    catalog_queue_service.process_queue(db_session, adapter_key=catalog_autofill.ADAPTER_KEY)

    assert calls == ["link", "registry", "relink"]
    db_session.refresh(task)
    assert [s["step"] for s in task.details["steps"]] == ["si_search", "link", "registry", "relink"]


def test_empty_manufacturers_go_first_and_enrichment_after_all(db_session, manufacturer, steps):
    from app.models.catalog_queue import CatalogLookupTask
    from sqlalchemy import select

    empty = Manufacturer(legal_name=f'ООО «Пустой {uuid.uuid4().hex[:8]}»', is_mirtek=False, market_share_pct=0.01)
    db_session.add(empty)
    db_session.commit()

    order = [m.id for m in catalog_autofill.queue_order(db_session)]
    assert order.index(empty.id) < order.index(manufacturer.id)

    catalog_autofill.enqueue_all(db_session)
    tasks = list(
        db_session.scalars(
            select(CatalogLookupTask)
            .where(CatalogLookupTask.manufacturer_id.in_([empty.id, manufacturer.id]))
            .order_by(CatalogLookupTask.created_at)
        )
    )
    keys = [t.adapter_key for t in tasks]
    assert keys == [catalog_autofill.ADAPTER_KEY] * 2 + [catalog_autofill.ENRICH_KEY] * 2
    status = {r["manufacturer_id"]: r for r in catalog_autofill.status_by_manufacturer(db_session)}
    assert status[empty.id]["enriching"] is True


def test_enqueue_all_during_a_run_does_not_requeue_finished(db_session, manufacturer, steps):
    """Повторный запуск во время идущего опроса присоединяется к нему: производитель, уже
    опрошенный в этом прогоне, заново в очередь не встаёт (28.09.2026 — МИРТЕК и Нартис
    после повторного запуска снова показывали «в очереди»)."""

    from sqlalchemy import select

    from app.models.catalog_queue import CatalogLookupTask

    waiting = Manufacturer(legal_name=f'ООО «Ждёт {uuid.uuid4().hex[:8]}»', is_mirtek=False)
    db_session.add(waiting)
    db_session.commit()
    catalog_autofill.enqueue(db_session, waiting, run_now=False)
    first = catalog_autofill.enqueue(db_session, manufacturer, run_now=False)
    catalog_queue_service.run_task(db_session, first)

    catalog_autofill.enqueue_all(db_session)

    tasks = list(
        db_session.scalars(
            select(CatalogLookupTask).where(
                CatalogLookupTask.adapter_key == catalog_autofill.ADAPTER_KEY,
                CatalogLookupTask.manufacturer_id == manufacturer.id,
            )
        )
    )
    assert [t.id for t in tasks] == [first.id]
