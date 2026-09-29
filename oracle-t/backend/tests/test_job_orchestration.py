"""Оркестрация разборов, отключение моделей и общий разбор записей одной закупки (29.09.2026)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import jobs
from app.models.ai_profile import AiProfileScore
from app.models.integration_setting import AiProviderSettings
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.source import Source
from app.models.tender import Tender
from app.models.user import User
from app.services import ai_client, ai_provider_service, tender_twins
from app.services.ai_context import JobCancelled, acting_as, running_job


def _tender(db, *, registry_number: str | None = None, source_type="etp_federal_commercial") -> Tender:
    source = Source(
        key=f"orch_{uuid.uuid4().hex[:8]}",
        name="Площадка для теста очереди",
        url="https://example.test",
        type=source_type,
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"ORCH-{uuid.uuid4().hex[:6]}",
        registry_number=registry_number,
        title="Поставка приборов учёта",
        currency="RUB",
    )
    db.add(tender)
    db.flush()
    return tender


def _user(db, name: str) -> User:
    user = User(username=f"orch_{uuid.uuid4().hex[:6]}", password_hash="x", full_name=name)
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def submitted(monkeypatch):
    """Пул не выполняет задачи — только запоминает, какие ему отдали."""

    sent: list[uuid.UUID] = []
    monkeypatch.setattr(jobs._executor, "submit", lambda fn, job_id: sent.append(job_id))
    monkeypatch.setattr(jobs, "_inflight", {})
    yield sent


# --- очередь по пользователям ------------------------------------------------------------


def test_second_tender_of_same_user_waits(db_session, submitted):
    """Пользователь разбирает закупку А — его закупка Б ждёт, пока А не закончится."""

    user = _user(db_session, "Холодов")
    first = jobs.enqueue(db_session, kind=JobKind.TENDER_FULL_REVIEW, tender=_tender(db_session), actor=user)
    second = jobs.enqueue(db_session, kind=JobKind.TENDER_FULL_REVIEW, tender=_tender(db_session), actor=user)

    assert submitted == [first.id]

    info = jobs.queue_info(db_session, [second])
    assert info[second.id] == ("own", 1)

    first.status = JobStatus.SUCCESS.value
    db_session.commit()
    jobs.dispatch(db_session)

    assert submitted == [first.id, second.id]


def test_other_users_run_in_parallel_up_to_the_limit(db_session, submitted, monkeypatch):
    monkeypatch.setattr(jobs.get_settings(), "jobs_max_parallel", 2)
    a, b, c = (_user(db_session, name) for name in ("А", "Б", "В"))

    job_a = jobs.enqueue(db_session, kind=JobKind.AI_PROFILE_SCORE, tender=_tender(db_session), actor=a)
    job_b = jobs.enqueue(db_session, kind=JobKind.AI_PROFILE_SCORE, tender=_tender(db_session), actor=b)
    job_c = jobs.enqueue(db_session, kind=JobKind.AI_PROFILE_SCORE, tender=_tender(db_session), actor=c)

    assert submitted == [job_a.id, job_b.id]
    job_a.status = JobStatus.RUNNING.value
    job_b.status = JobStatus.RUNNING.value
    db_session.commit()
    assert jobs.queue_info(db_session, [job_c])[job_c.id] == ("slot", 2)


def test_user_queue_endpoint_shows_own_and_admin_sees_everyone(db_session, submitted):
    from app.services import job_queue_service

    user = _user(db_session, "Мурашова")
    tender = _tender(db_session)
    jobs.enqueue(db_session, kind=JobKind.TENDER_FULL_REVIEW, tender=tender, actor=user)
    jobs.enqueue(db_session, kind=JobKind.TENDER_FULL_REVIEW, tender=_tender(db_session), actor=user)

    mine = job_queue_service.user_queues(db_session, user_id=user.id, everyone=False)
    assert len(mine) == 1
    assert mine[0].user_name == "Мурашова"
    assert len(mine[0].queued) == 2  # пул в тесте их не запускает — обе «в очереди»
    assert mine[0].queued[0].tender_id == tender.id

    everyone = job_queue_service.user_queues(db_session, everyone=True)
    assert any(queue.user_id == user.id for queue in everyone)
    assert job_queue_service.launched_by(db_session, user.id)[0].kind_label == "Полный разбор"


# --- отключение моделей ---------------------------------------------------------------------


@pytest.fixture()
def providers(db_session, monkeypatch):
    """Все три модели «настроены»; настройки — в сессии теста."""

    monkeypatch.setattr(ai_provider_service, "_is_configured", lambda db, provider: True)
    settings = db_session.get(AiProviderSettings, ai_provider_service._SINGLETON_ID)
    if settings is None:
        settings = AiProviderSettings(id=ai_provider_service._SINGLETON_ID, active_provider="yandex")
        db_session.add(settings)
    settings.active_provider = "yandex"
    settings.disabled_providers = {}
    db_session.flush()
    return settings


def test_disabled_model_is_replaced_for_new_requests(db_session, admin_user, providers):
    user = _user(db_session, "Выбрал DeepSeek")
    user.ai_provider = "deepseek"
    db_session.flush()

    ai_provider_service.set_provider_enabled(db_session, "deepseek", False, actor=admin_user)

    assert ai_provider_service.resolve_provider(db_session, user) == ("yandex", "default")
    status = ai_provider_service.get_status(db_session, user)
    assert status.disabled_providers == ["deepseek"]
    assert status.requested_provider == "deepseek"
    with pytest.raises(ai_provider_service.AiModelDisabledError):
        ai_provider_service.set_user_provider(db_session, user, "deepseek")

    ai_provider_service.set_provider_enabled(db_session, "deepseek", True, actor=admin_user)
    assert ai_provider_service.resolve_provider(db_session, user) == ("deepseek", "user")


def test_job_started_before_disabling_is_stopped(db_session, admin_user, providers, monkeypatch):
    """Задача, начатая до выключения модели, не переезжает молча на другую — она
    останавливается, и ни один запрос в выключенную модель больше не уходит."""

    calls: list[str] = []
    monkeypatch.setattr(ai_client.routerai_client, "run_structured", lambda *a, **k: calls.append("routerai"))
    monkeypatch.setattr(ai_client.yandex_ai_client, "run_structured", lambda *a, **k: calls.append("yandex"))
    user = _user(db_session, "Выбрал DeepSeek")
    user.ai_provider = "deepseek"
    db_session.flush()

    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    ai_provider_service.set_provider_enabled(db_session, "deepseek", False, actor=admin_user)

    with acting_as(user.id), running_job(started), pytest.raises(JobCancelled):
        ai_client.run_structured(db_session, system_prompt="", user_text="", response_model=None)
    assert calls == []

    # Задача, начатая уже после выключения, сразу идёт через замену.
    with acting_as(user.id), running_job(datetime.now(timezone.utc)):
        ai_client.run_structured(db_session, system_prompt="", user_text="", response_model=None)
    assert calls == ["yandex"]


def test_all_models_disabled_refuses_with_clear_text(db_session, admin_user, providers):
    for provider in ("yandex", "claude", "deepseek"):
        ai_provider_service.set_provider_enabled(db_session, provider, False, actor=admin_user)

    with pytest.raises(ai_provider_service.AiModelDisabledError, match="отключены администратором"):
        ai_client.run_structured(db_session, system_prompt="", user_text="", response_model=None)


def test_cancelled_job_is_marked_and_not_retried(db_session, admin_user, monkeypatch):
    tender = _tender(db_session)
    attempts: list[int] = []

    def cancelled_handler(db, t, actor):
        attempts.append(1)
        raise JobCancelled("DeepSeek отключена администратором во время разбора — задача остановлена.")

    monkeypatch.setitem(jobs._HANDLERS, JobKind.AI_PROFILE_SCORE.value, cancelled_handler)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    job = BackgroundJob(kind=JobKind.AI_PROFILE_SCORE.value, status="queued", tender_id=tender.id)
    db_session.add(job)
    db_session.commit()

    jobs.run_job(job.id)

    assert job.status == JobStatus.CANCELLED.value
    assert "отключена" in job.message
    assert attempts == [1]


# --- общий разбор записей одной закупки --------------------------------------------------


def test_twin_shows_the_analysed_record(db_session):
    """Закупка разобрана по записи из ЕИС — запись из Госплана показывает тот же разбор."""

    number = "3" + uuid.uuid4().hex[:10].translate(str.maketrans("abcdef", "123456"))
    eis = _tender(db_session, registry_number=number, source_type="eis")
    db_session.add(
        AiProfileScore(tender_id=eis.id, overall_score=63, verdict="participate", is_current=True)
    )
    gosplan = _tender(db_session, registry_number=number, source_type="gosplan")
    db_session.flush()

    assert tender_twins.link(db_session, gosplan).id == eis.id
    assert gosplan.analysis_tender_id == eis.id
    assert eis.analysis_tender_id is None

    from app.services.analysis_service import attach_analysis_fields

    row = attach_analysis_fields(db_session, [gosplan])[0]
    assert float(row["ai_score"]) == 63


def test_review_from_twin_joins_the_running_review(client, admin_token, db_session, monkeypatch):
    """Пользователь Б жмёт «Разобрать» на записи из Госплана, пока А разбирает ту же
    закупку из ЕИС, — второй разбор не создаётся."""

    from app.db.session import SessionLocal

    monkeypatch.setattr(jobs._executor, "submit", lambda *args, **kwargs: None)
    number = "3" + uuid.uuid4().hex[:10].translate(str.maketrans("abcdef", "123456"))
    db = SessionLocal()
    try:
        eis = _tender(db, registry_number=number, source_type="eis")
        gosplan = _tender(db, registry_number=number, source_type="gosplan")
        db.commit()
        headers = {"Authorization": f"Bearer {admin_token}"}

        first = client.post(f"/tenders/{eis.id}/review", headers=headers).json()
        second = client.post(f"/tenders/{gosplan.id}/ai-score", headers=headers)
        if second.status_code == 400:  # профиль компании не заполнен в тестовой базе
            second = client.post(f"/tenders/{gosplan.id}/review", headers=headers)
        assert second.json()["id"] == first["id"], (first, second.json())
        assert second.json()["tender_id"] == first["tender_id"]

        polled = client.get(f"/jobs?tender_id={gosplan.id}", headers=headers).json()
        assert [job["id"] for job in polled] == [first["id"]]
    finally:
        db.query(BackgroundJob).filter(BackgroundJob.tender_id.in_([eis.id, gosplan.id])).delete()
        db.query(Tender).filter(Tender.id.in_([gosplan.id, eis.id])).update({"analysis_tender_id": None})
        db.query(Tender).filter(Tender.id.in_([gosplan.id, eis.id])).delete()
        db.commit()
        db.close()
