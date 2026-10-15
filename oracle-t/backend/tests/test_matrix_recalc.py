"""Массовый пересчёт матрицы соответствия (15.10.2026): охват, оценка стоимости, запуск."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete

import app.services.matrix_recalc_service as recalc
from app.models.ai_usage import AiUsageRecord
from app.models.analysis import WinPercentage
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.manufacturer import Manufacturer
from app.models.source import Source
from app.models.tender import Tender

NOW = datetime.now(timezone.utc)


def _tender(db_session, *, ends: datetime | None, with_matrix: bool) -> Tender:
    suffix = uuid.uuid4().hex[:8]
    source = Source(key=f"rc_{suffix}", name="Источник", url=f"https://example.test/{suffix}", type="eis")
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"R-{suffix}", title="Поставка счетчиков", application_end=ends)
    db_session.add(tender)
    db_session.flush()
    if with_matrix:
        manufacturer = Manufacturer(legal_name=f"ООО «Р {suffix}»", brand_name=f"Р{suffix}")
        db_session.add(manufacturer)
        db_session.flush()
        db_session.add(
            WinPercentage(
                tender_id=tender.id, manufacturer_id=manufacturer.id, percentage=Decimal("50"), is_current=True
            )
        )
    db_session.commit()
    return tender


def test_scope_takes_tenders_with_matrix_and_open_ones(db_session):
    open_one = _tender(db_session, ends=NOW + timedelta(days=5), with_matrix=True)
    past = _tender(db_session, ends=NOW - timedelta(days=5), with_matrix=True)
    no_matrix = _tender(db_session, ends=NOW + timedelta(days=5), with_matrix=False)

    opened = recalc.scope_tender_ids(db_session, "open")
    everything = recalc.scope_tender_ids(db_session, "all")

    assert open_one.id in opened and past.id not in opened and no_matrix.id not in opened
    assert {open_one.id, past.id} <= set(everything) and no_matrix.id not in everything


def test_estimate_uses_actual_usage_per_job(db_session):
    db_session.execute(delete(AiUsageRecord))
    db_session.execute(delete(BackgroundJob))
    first, second = uuid.uuid4(), uuid.uuid4()
    for job_id, tokens, cost in ((first, 600, "1.5"), (first, 400, "0.5"), (second, 3000, "4")):
        db_session.add(
            AiUsageRecord(
                provider="deepseek", operation="ComplianceResult", job_id=job_id, job_kind="tender_evaluation",
                prompt_tokens=tokens, completion_tokens=0, status="success", cost=Decimal(cost),
            )
        )
    db_session.add(
        BackgroundJob(
            kind=JobKind.TENDER_EVALUATION.value, status=JobStatus.SUCCESS.value,
            started_at=NOW - timedelta(minutes=10), finished_at=NOW - timedelta(minutes=4),
        )
    )
    db_session.commit()

    average = recalc.per_tender(db_session, "evaluation")
    assert (average.sample, average.tokens, average.cost, average.minutes) == (2, 2000, 3.0, 6.0)
    # Полных разборов в истории нет — оценки нет, а не ноль.
    assert recalc.per_tender(db_session, "review").sample == 0
    assert recalc.per_tender(db_session, "review").tokens is None


def test_launch_skips_tenders_already_in_work(db_session, monkeypatch):
    free = _tender(db_session, ends=NOW + timedelta(days=3), with_matrix=True)
    busy = _tender(db_session, ends=NOW + timedelta(days=3), with_matrix=True)
    db_session.add(BackgroundJob(kind=JobKind.TENDER_FULL_REVIEW.value, status=JobStatus.RUNNING.value, tender_id=busy.id))
    db_session.commit()

    queued: list = []
    monkeypatch.setattr(recalc, "enqueue", lambda db, *, kind, tender, actor: queued.append((kind, tender.id)))
    result = recalc.launch(db_session, "evaluation", "open", None)

    assert (JobKind.TENDER_EVALUATION, free.id) in queued
    assert all(tender_id != busy.id for _, tender_id in queued)
    assert result["already_running"] >= 1 and result["queued"] == len(queued)


def test_unknown_mode_is_rejected(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.get("/matrix-recalc/estimate?mode=all&scope=open", headers=headers)
    assert response.status_code == 400 and "Режим" in response.json()["detail"]

    response = client.get("/matrix-recalc/estimate?mode=evaluation&scope=open", headers=headers)
    assert response.status_code == 200
    assert {"tenders", "per_tender", "total", "parallel"} <= set(response.json())
