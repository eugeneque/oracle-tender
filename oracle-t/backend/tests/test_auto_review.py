"""Автоматический разбор закупок через YandexGPT (30.10.2026, раздел 81 ARCHITECTURE.md)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import jobs
from app.models.ai_profile import AiProfileScore
from app.models.integration_setting import AiProviderSettings
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.source import Source
from app.models.tender import Tender, TenderStage
from app.models.user import User
from app.services import ai_client, ai_provider_service, auto_review_service
from app.services.ai_context import acting_as, forcing_provider


def _tender(db, *, hours: float = 1, **fields) -> Tender:
    source = Source(
        key=f"auto_{uuid.uuid4().hex[:8]}",
        name="Площадка для теста авторазбора",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    # Срок подачи — в ближайший час: такие закупки идут в очереди первыми, и данные
    # соседних тестов в общей базе порядок не сбивают.
    values = {
        "passed_relevance_filter": True,
        "stage": TenderStage.AI_SELECTED.value,
        "application_end": datetime.now(timezone.utc) + timedelta(hours=hours),
        **fields,
    }
    tender = Tender(
        source_id=source.id,
        external_id=f"AUTO-{uuid.uuid4().hex[:6]}",
        title="Поставка приборов учёта",
        currency="RUB",
        **values,
    )
    db.add(tender)
    db.flush()
    return tender


@pytest.fixture()
def submitted(monkeypatch):
    """Пул не выполняет задачи — только запоминает, какие ему отдали."""

    sent: list[uuid.UUID] = []
    monkeypatch.setattr(jobs._executor, "submit", lambda fn, job_id: sent.append(job_id))
    monkeypatch.setattr(jobs, "_inflight", {})
    yield sent


@pytest.fixture()
def providers(client, db_session, monkeypatch):
    """Все модели «настроены», по умолчанию — DeepSeek. `client` — ради бутстрапа
    администратора, от имени которого пишется журнал."""

    monkeypatch.setattr(ai_provider_service, "_is_configured", lambda db, provider: True)
    settings = db_session.get(AiProviderSettings, ai_provider_service._SINGLETON_ID)
    if settings is None:
        settings = AiProviderSettings(id=ai_provider_service._SINGLETON_ID, active_provider="yandex")
        db_session.add(settings)
    settings.active_provider = "deepseek"
    settings.disabled_providers = {}
    settings.auto_review_enabled = False
    db_session.flush()
    return settings


def _candidate_ids(db) -> list[uuid.UUID]:
    return [row.subject_id for row in db.execute(auto_review_service._candidates(db))]


def test_candidates_are_open_relevant_and_unreviewed(db_session, providers):
    good = _tender(db_session)
    rejected_by_model = _tender(db_session, ai_relevant=False)
    not_in_profiles = _tender(db_session, passed_relevance_filter=False)
    closed = _tender(db_session, hours=-1)
    rejected_stage = _tender(db_session, stage=TenderStage.REJECTED.value)
    reviewed = _tender(db_session)
    db_session.add(AiProfileScore(tender_id=reviewed.id, is_current=True))
    db_session.flush()

    ids = _candidate_ids(db_session)
    assert good.id in ids
    for tender in (rejected_by_model, not_in_profiles, closed, rejected_stage, reviewed):
        assert tender.id not in ids


def test_twin_records_give_one_candidate_of_the_primary(db_session, providers):
    primary = _tender(db_session, passed_relevance_filter=False)
    twin = _tender(db_session, analysis_tender_id=primary.id)

    ids = _candidate_ids(db_session)
    assert primary.id in ids
    assert twin.id not in ids


def test_feed_queues_one_tender_at_a_time_with_yandex(db_session, admin_user, providers, submitted):
    first = _tender(db_session, hours=0.5)
    second = _tender(db_session, hours=0.6)

    assert auto_review_service.feed(db_session) is None  # выключен — ничего не ставится

    state = auto_review_service.set_enabled(db_session, True, actor=admin_user)
    assert state["enabled"] is True
    job = db_session.scalar(
        select(BackgroundJob).where(BackgroundJob.tender_id == first.id)
    )
    assert job.kind == JobKind.TENDER_FULL_REVIEW.value
    assert job.payload == {"auto": True, "provider": "yandex"}
    assert job.created_by_id is None
    assert state["current"]["tender_id"] == first.id

    # Пока первая не закончилась, вторая не ставится.
    assert auto_review_service.feed(db_session) is None

    job.status = JobStatus.ERROR.value
    db_session.commit()
    second_job = auto_review_service.feed(db_session)
    assert second_job.tender_id == second.id
    # Неудачная попытка по кругу не повторяется.
    assert first.id not in _candidate_ids(db_session)


def test_feed_stops_when_yandex_is_disabled(db_session, admin_user, providers, submitted):
    _tender(db_session)
    providers.auto_review_enabled = True
    ai_provider_service.set_provider_enabled(db_session, "yandex", False, actor=admin_user)

    assert auto_review_service.feed(db_session) is None
    assert "отключена" in auto_review_service.status(db_session)["blocker"]


def test_forced_provider_beats_personal_choice_and_is_not_substituted(
    db_session, admin_user, providers, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(ai_client.routerai_client, "run_structured", lambda *a, **k: calls.append("routerai"))
    monkeypatch.setattr(ai_client.yandex_ai_client, "run_structured", lambda *a, **k: calls.append("yandex"))
    user = User(username=f"auto_{uuid.uuid4().hex[:6]}", password_hash="x", full_name="Выбрал Qwen")
    user.ai_provider = "qwen"
    db_session.add(user)
    db_session.flush()

    with acting_as(user.id), forcing_provider("yandex"):
        ai_client.run_structured(db_session, system_prompt="", user_text="", response_model=None)
    assert calls == ["yandex"]

    # Выключенный Yandex не подменяется платной моделью — запрос отказывает.
    ai_provider_service.set_provider_enabled(db_session, "yandex", False, actor=admin_user)
    with forcing_provider("yandex"), pytest.raises(ai_provider_service.AiModelDisabledError):
        ai_client.run_structured(db_session, system_prompt="", user_text="", response_model=None)
    assert calls == ["yandex"]


def test_endpoints_are_admin_only(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.get("/integrations/auto-review", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "yandex"
    assert client.get("/integrations/auto-review").status_code == 401
