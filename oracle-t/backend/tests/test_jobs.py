"""Тесты фоновой очереди анализа и расчёта (раздел 5.9 ТЗ).

Проверяется то, ради чего очередь и заводилась: пользователь не ждёт ответа, повторное
нажатие кнопки не запускает второй разбор того же тендера поверх первого, сбой повторяется
автоматически, а после падения сервера «выполняется» не висит вечно.

Обработчики подменяются: настоящие ходят в YandexGPT, а здесь проверяется механика очереди,
а не качество ответа модели.
"""

from __future__ import annotations

import uuid

import pytest

from app.core import jobs
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.source import Source
from app.models.tender import Tender


def _tender(db) -> Tender:
    source = Source(
        key=f"jobs_{uuid.uuid4().hex[:8]}",
        name="Площадка для теста задач",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"JOB-{uuid.uuid4().hex[:6]}",
        title="Поставка приборов учёта",
        currency="RUB",
    )
    db.add(tender)
    db.flush()
    return tender


@pytest.fixture()
def no_background_execution(monkeypatch):
    """Задача не должна улетать в реальный пул: в тестах она выполняется вручную, иначе
    отдельный поток открыл бы свою сессию к БД и не увидел незакоммиченную транзакцию теста."""

    monkeypatch.setattr(jobs._executor, "submit", lambda *args, **kwargs: None)


def test_enqueue_creates_queued_job(db_session, admin_user, no_background_execution):
    tender = _tender(db_session)

    job = jobs.enqueue(
        db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user
    )

    assert job.status == JobStatus.QUEUED.value
    assert job.tender_id == tender.id
    assert job.created_by_id == admin_user.id


def test_second_enqueue_returns_the_same_job(db_session, admin_user, no_background_execution):
    """Двойное нажатие кнопки «Анализ документов» не должно порождать два параллельных
    разбора одного тендера — второй затирал бы требования, сохранённые первым."""

    tender = _tender(db_session)

    first = jobs.enqueue(db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user)
    second = jobs.enqueue(db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user)

    assert first.id == second.id


def test_different_kinds_are_queued_separately(db_session, admin_user, no_background_execution):
    tender = _tender(db_session)

    analysis = jobs.enqueue(
        db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user
    )
    evaluation = jobs.enqueue(
        db_session, kind=JobKind.TENDER_EVALUATION, tender=tender, actor=admin_user
    )

    assert analysis.id != evaluation.id


def test_successful_run_stores_handler_message(
    db_session, admin_user, monkeypatch, no_background_execution
):
    tender = _tender(db_session)
    job = jobs.enqueue(db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user)

    monkeypatch.setitem(
        jobs._HANDLERS, JobKind.TENDER_ANALYSIS.value, lambda db, t, actor: "требований: 7"
    )
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    jobs.run_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.SUCCESS.value
    assert job.message == "требований: 7"
    assert job.attempts == 1
    assert job.finished_at is not None


def test_failing_handler_is_retried_then_marked_error(
    db_session, admin_user, monkeypatch, no_background_execution
):
    """Сбой модели повторяется автоматически (раздел 5.9 ТЗ). Если не помогло — задача
    честно помечается ошибкой с текстом причины, а не остаётся «выполняется»."""

    tender = _tender(db_session)
    job = jobs.enqueue(db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user)

    calls = {"count": 0}

    def _always_fails(db, t, actor):
        calls["count"] += 1
        raise RuntimeError("модель недоступна")

    monkeypatch.setitem(jobs._HANDLERS, JobKind.TENDER_ANALYSIS.value, _always_fails)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(jobs, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(db_session, "close", lambda: None)

    jobs.run_job(job.id)

    db_session.refresh(job)
    assert calls["count"] == jobs.MAX_ATTEMPTS
    assert job.status == JobStatus.ERROR.value
    assert "модель недоступна" in (job.message or "")


def test_second_attempt_can_succeed(
    db_session, admin_user, monkeypatch, no_background_execution
):
    tender = _tender(db_session)
    job = jobs.enqueue(db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user)

    calls = {"count": 0}

    def _fails_once(db, t, actor):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("временный сбой сети")
        return "готово со второй попытки"

    monkeypatch.setitem(jobs._HANDLERS, JobKind.TENDER_ANALYSIS.value, _fails_once)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(jobs, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(db_session, "close", lambda: None)

    jobs.run_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.SUCCESS.value
    assert job.attempts == 2


def test_interrupted_jobs_are_recovered_on_startup(
    db_session, admin_user, monkeypatch, no_background_execution
):
    """После перезапуска сервера пул пуст, а в базе остались «выполняется» — карточка
    тендера показывала бы вечный индикатор, если их не закрыть."""

    tender = _tender(db_session)
    job = jobs.enqueue(db_session, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=admin_user)
    job.status = JobStatus.RUNNING.value
    db_session.commit()

    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    jobs.recover_interrupted_jobs()

    db_session.refresh(job)
    assert job.status == JobStatus.ERROR.value
    assert "перезапуск" in (job.message or "").lower()


def test_analyze_endpoint_returns_job_without_waiting(client, admin_token, monkeypatch):
    """HTTP-ответ приходит сразу: эндпоинт только ставит задачу в очередь."""

    monkeypatch.setattr(jobs._executor, "submit", lambda *args, **kwargs: None)

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        tender = _tender(db)
        db.commit()
        tender_id = tender.id
    finally:
        db.close()

    response = client.post(
        f"/tenders/{tender_id}/analyze", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == JobKind.TENDER_ANALYSIS.value
    assert body["status"] in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}

    listing = client.get(
        f"/jobs?tender_id={tender_id}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert listing.status_code == 200
    assert any(item["id"] == body["id"] for item in listing.json())

    db = SessionLocal()
    try:
        db.query(BackgroundJob).filter(BackgroundJob.tender_id == tender_id).delete()
        db.query(Tender).filter(Tender.id == tender_id).delete()
        db.commit()
    finally:
        db.close()


def test_full_review_runs_steps_and_skips_matrix_without_product_requirements(
    db_session, admin_user, monkeypatch, no_background_execution
):
    """Полный разбор (18.09.2026): анализ → матрица → оценка одной задачей. У закупки на
    услуги требований к товару нет — матрица пропускается, а не падает с «сначала выполните
    анализ»; итог каждого шага лежит в `payload`, ход шагов — в `message`."""

    from app.models.analysis import Requirement
    from app.services import job_runner

    tender = _tender(db_session)
    job = jobs.enqueue(
        db_session, kind=JobKind.TENDER_FULL_REVIEW, tender=tender, actor=admin_user
    )
    progress: list[str] = []
    original_commit = db_session.commit

    def commit_and_record():
        original_commit()
        if job.message and job.message.startswith("Шаг"):
            progress.append(job.message)

    def fake_analysis(db, t, actor):
        db.add(Requirement(tender_id=t.id, text="Гарантия на работы", kind="service"))
        db.flush()
        return "требований сохранено: 1 (к услугам: 1)"

    evaluation_calls: list[str] = []
    monkeypatch.setattr(job_runner, "_run_analysis", fake_analysis)
    monkeypatch.setattr(
        job_runner, "_run_evaluation", lambda db, t, actor: evaluation_calls.append("x") or "?"
    )
    monkeypatch.setattr(job_runner, "_run_profile_score", lambda db, t, actor: "итоговая оценка: 40%")
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", commit_and_record)

    jobs.run_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.SUCCESS.value
    assert evaluation_calls == [], "без требований к товару матрица не считается"
    assert job.payload["analysis"] == "требований сохранено: 1 (к услугам: 1)"
    assert job.payload["evaluation"].startswith("пропущен")
    assert job.payload["score"] == "итоговая оценка: 40%"
    assert "Анализ:" in job.message and "Оценка: итоговая оценка: 40%" in job.message
    assert progress[:1] == ["Шаг 1 из 3: анализ документов…"]
    assert "Шаг 3 из 3: AI-оценка по профилю…" in progress


def test_full_review_fails_only_when_both_analysis_and_score_fail(
    db_session, admin_user, monkeypatch, no_background_execution
):
    from app.services import job_runner

    tender = _tender(db_session)
    job = jobs.enqueue(
        db_session, kind=JobKind.TENDER_FULL_REVIEW, tender=tender, actor=admin_user
    )

    def boom(db, t, actor):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(job_runner, "_run_analysis", boom)
    monkeypatch.setattr(job_runner, "_run_profile_score", boom)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    jobs.run_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.ERROR.value
    assert "модель недоступна" in job.message


def test_jobs_endpoint_requires_auth(client):
    assert client.get("/jobs").status_code == 401


def test_enqueue_standalone_dedupes_running_poll(db_session, admin_user, monkeypatch):
    """Повторное нажатие «Синхронизировать», пока опрос идёт, возвращает ту же задачу:
    второй опрос тех же площадок поверх первого только удвоил бы нагрузку и ожидание."""

    monkeypatch.setattr(jobs, "_submit_poll", lambda job_id: None)

    first = jobs.enqueue_standalone(
        db_session, kind=JobKind.SOURCES_POLL, payload={"source_keys": ["eis"]}, actor=admin_user
    )
    second = jobs.enqueue_standalone(
        db_session, kind=JobKind.SOURCES_POLL, payload={"source_keys": ["zakazrf"]}, actor=admin_user
    )
    assert second.id == first.id
    assert first.tender_id is None
    assert first.payload == {"source_keys": ["eis"]}
    assert jobs.latest_standalone(db_session, JobKind.SOURCES_POLL).id == first.id

    # Завершённая задача больше не «занимает» очередь — следующий вызов создаёт новую.
    first.status = JobStatus.SUCCESS.value
    db_session.flush()
    third = jobs.enqueue_standalone(
        db_session, kind=JobKind.SOURCES_POLL, payload={"source_keys": ["eis"]}, actor=admin_user
    )
    assert third.id != first.id
