"""Бреши разбора закупки (28.09.2026): оборванный «Полный разбор» оставил закупку с
извлечёнными требованиями, но без матрицы соответствия, а «Обновить» её не строил —
заключение называло наши приборы «не проверен по ТЗ». Здесь проверяется, что пересчёт
сам достраивает недостающие шаги и что полный разбор продолжает с оборванного шага.

Обработчики шагов подменяются: настоящие ходят в модель ИИ.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.analysis import RequirementKind, WinPercentage
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.manufacturer import Manufacturer
from app.services import job_runner
from tests.test_ai_profile import _requirement, _tender


def _product_requirement(db, tender):
    requirement = _requirement(db, tender, "Класс точности 1")
    requirement.kind = RequirementKind.PRODUCT.value
    db.flush()
    return requirement


def _full_matrix(db, tender, *, calculated_at: datetime | None = None) -> None:
    if not db.scalars(select(Manufacturer)).first():
        db.add(Manufacturer(legal_name="ООО «Тест матрицы»"))
        db.flush()
    for manufacturer in db.scalars(select(Manufacturer)):
        row = WinPercentage(
            tender_id=tender.id,
            manufacturer_id=manufacturer.id,
            percentage=Decimal("80"),
            is_current=True,
        )
        if calculated_at is not None:
            row.calculated_at = calculated_at
        db.add(row)
    db.flush()


def test_no_product_requirements_means_no_matrix_needed(db_session):
    tender = _tender(db_session)
    requirement = _requirement(db_session, tender, "Опыт поставок не менее 3 лет")
    requirement.kind = RequirementKind.PARTICIPANT.value
    db_session.flush()

    assert job_runner.matrix_gap(db_session, tender) is None


def test_missing_matrix_is_a_gap(db_session):
    tender = _tender(db_session)
    _product_requirement(db_session, tender)

    assert "не построена" in (job_runner.matrix_gap(db_session, tender) or "")


def test_fresh_full_matrix_is_not_a_gap(db_session):
    tender = _tender(db_session)
    _product_requirement(db_session, tender)
    _full_matrix(db_session, tender)

    assert job_runner.matrix_gap(db_session, tender) is None


def test_matrix_older_than_requirements_is_a_gap(db_session):
    tender = _tender(db_session)
    _product_requirement(db_session, tender)
    _full_matrix(
        db_session, tender, calculated_at=datetime.now(timezone.utc) - timedelta(days=1)
    )

    assert "обновлены" in (job_runner.matrix_gap(db_session, tender) or "")


def test_prepare_builds_missing_matrix(db_session, monkeypatch):
    tender = _tender(db_session)
    _product_requirement(db_session, tender)
    calls: list[str] = []
    monkeypatch.setattr(job_runner, "_run_analysis", lambda db, t, a: calls.append("analysis") or "")
    monkeypatch.setattr(
        job_runner, "_run_evaluation", lambda db, t, a, **kw: calls.append("evaluation") or "ячеек: 1"
    )

    messages = job_runner.prepare_tender(db_session, tender, None)

    assert calls == ["evaluation"]
    assert "матрица достроена" in messages[0]


def test_prepare_runs_analysis_when_nothing_extracted(db_session, monkeypatch):
    tender = _tender(db_session)
    calls: list[str] = []
    monkeypatch.setattr(job_runner, "_run_analysis", lambda db, t, a: calls.append("analysis") or "")
    monkeypatch.setattr(job_runner, "_run_evaluation", lambda db, t, a, **kw: calls.append("evaluation") or "")

    job_runner.prepare_tender(db_session, tender, None)

    assert calls == ["analysis"]


def test_full_review_resumes_after_finished_analysis(db_session, monkeypatch):
    """Перезапуск сервера посреди матрицы: анализ не повторяется, матрица строится."""

    tender = _tender(db_session)
    _product_requirement(db_session, tender)
    job = BackgroundJob(
        kind=JobKind.TENDER_FULL_REVIEW.value,
        status=JobStatus.RUNNING.value,
        tender_id=tender.id,
        payload={"analysis": "требований сохранено: 1", "restarts": 1},
    )
    db_session.add(job)
    db_session.flush()
    calls: list[str] = []
    monkeypatch.setattr(job_runner, "_run_analysis", lambda db, t, a: calls.append("analysis") or "")
    monkeypatch.setattr(
        job_runner, "_run_evaluation", lambda db, t, a, **kw: calls.append("evaluation") or "ячеек: 1"
    )
    monkeypatch.setattr(
        job_runner, "_run_profile_score", lambda db, t, a: calls.append("score") or "оценка"
    )

    summary = job_runner._run_full_review(db_session, job, None)

    assert calls == ["evaluation", "score"]
    assert "Анализ: требований сохранено: 1" in summary
