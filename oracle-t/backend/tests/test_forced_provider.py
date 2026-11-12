"""Модель, закреплённая за задачей (`ai_context.forcing_provider`): важнее личного выбора и не
подменяется выключенной. Им пользуется выбор модели матрицы (раздел 104); авторазбор, для
которого механизм появился, удалён 12.11.2026 (раздел 105)."""

from __future__ import annotations

import uuid

import pytest

from app.models.integration_setting import AiProviderSettings
from app.models.user import User
from app.services import ai_client, ai_provider_service
from app.services.ai_context import acting_as, forcing_provider


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
    db_session.flush()
    return settings


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


def test_auto_review_endpoints_are_gone(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.get("/integrations/auto-review", headers=headers).status_code == 404
