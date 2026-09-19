"""Переключатель ИИ-провайдера (18.09.2026): YandexGPT ↔ Claude через RouterAI.

Живой шлюз в тестах не зовём — проверяется логика вокруг: кто вправе переключать, что
нельзя включить ненастроенного провайдера, что ключ не утекает наружу, и что диспетчер
`ai_client.run_structured` действительно отправляет запрос выбранному клиенту, а не обоим.
"""

import uuid

import pydantic

from app.models.user import User
from app.services import ai_client, ai_provider_service


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_regular_user_token(client, admin_token) -> str:
    username = f"aiprov_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "T", "role": "user"},
        headers=_auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/auth/login", json={"username": username, "password": "SomePass123"})
    return login.json()["access_token"]


def _reset(client, headers) -> None:
    # Обе таблицы — синглтоны, тестовая БД между запусками не пересоздаётся.
    client.patch("/integrations/yandex-ai-studio", json={"api_key": "", "folder_id": ""}, headers=headers)
    client.patch("/integrations/routerai", json={"api_key": "", "model": "", "base_url": ""}, headers=headers)


def test_status_visible_to_any_user_but_switch_is_admin_only(client, admin_token):
    headers = _auth_headers(admin_token)
    user_headers = _auth_headers(_create_regular_user_token(client, admin_token))

    assert client.get("/integrations/ai-provider").status_code == 401
    status = client.get("/integrations/ai-provider", headers=user_headers)
    assert status.status_code == 200, status.text
    assert status.json()["active_provider"] in {"yandex", "claude"}
    assert "api_key" not in status.text

    assert (
        client.put("/integrations/ai-provider", json={"active_provider": "claude"}, headers=user_headers)
        .status_code
        == 403
    )
    assert client.get("/integrations/routerai", headers=user_headers).status_code == 403
    assert client.get("/integrations/routerai", headers=headers).status_code == 200


def test_cannot_switch_to_unconfigured_provider(client, admin_token):
    headers = _auth_headers(admin_token)
    _reset(client, headers)

    resp = client.put("/integrations/ai-provider", json={"active_provider": "claude"}, headers=headers)
    assert resp.status_code == 400, resp.text
    assert "Claude" in resp.json()["detail"]

    resp = client.put("/integrations/ai-provider", json={"active_provider": "gpt"}, headers=headers)
    assert resp.status_code == 422


def test_switch_and_routerai_settings_roundtrip(client, admin_token):
    headers = _auth_headers(admin_token)
    _reset(client, headers)

    saved = client.patch(
        "/integrations/routerai",
        json={"api_key": "sk-secretkey-abcd", "model": "anthropic/claude-opus-5"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["is_configured"] is True
    assert body["api_key_masked"] == "••••••••abcd"
    assert "secret" not in saved.text
    assert body["model"] == "anthropic/claude-opus-5"
    assert body["base_url"] == ai_provider_service.DEFAULT_ROUTERAI_BASE_URL

    # PATCH только модели не трогает ключ; пустая строка возвращает значение по умолчанию.
    again = client.patch("/integrations/routerai", json={"model": ""}, headers=headers).json()
    assert again["api_key_masked"] == "••••••••abcd"
    assert again["model"] == ai_provider_service.DEFAULT_ROUTERAI_MODEL

    switched = client.put(
        "/integrations/ai-provider", json={"active_provider": "claude"}, headers=headers
    )
    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["active_provider"] == "claude"
    assert body["label"] == "Claude"
    assert body["model"] == ai_provider_service.DEFAULT_ROUTERAI_MODEL
    assert body["is_configured"] is True
    assert body["source"] == "default"
    assert body["default_provider"] == "claude"
    assert body["configured_providers"] == ["claude"]

    # Обратно на Yandex — только когда его данные заполнены.
    back = client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)
    assert back.status_code == 400
    client.patch(
        "/integrations/yandex-ai-studio",
        json={"api_key": "AQVNkey", "folder_id": "b1gfolder"},
        headers=headers,
    )
    back = client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)
    assert back.status_code == 200, back.text
    assert back.json()["active_provider"] == "yandex"
    assert back.json()["model"] is None


def test_test_connection_reports_missing_key_without_network(client, admin_token):
    headers = _auth_headers(admin_token)
    _reset(client, headers)
    resp = client.post("/integrations/routerai/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is False
    assert "не настроено" in resp.json()["message"]


class _Answer(pydantic.BaseModel):
    text: str


def test_personal_choice_overrides_default_only_for_that_user(client, admin_token):
    """Один пользователь работает с Claude, другой — с моделью по умолчанию (Yandex)."""

    headers = _auth_headers(admin_token)
    _reset(client, headers)
    client.patch("/integrations/yandex-ai-studio", json={"api_key": "AQVNkey", "folder_id": "b1g"}, headers=headers)
    client.patch("/integrations/routerai", json={"api_key": "sk-abcd"}, headers=headers)
    client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)
    client.put("/auth/me/ai-provider", json={"ai_provider": None}, headers=headers)

    user_headers = _auth_headers(_create_regular_user_token(client, admin_token))

    # Пользователь выбирает Claude — у него меняется, у администратора нет.
    mine = client.put("/auth/me/ai-provider", json={"ai_provider": "claude"}, headers=user_headers)
    assert mine.status_code == 200, mine.text
    assert mine.json()["active_provider"] == "claude"
    assert mine.json()["source"] == "user"
    assert mine.json()["default_provider"] == "yandex"

    admin_view = client.get("/integrations/ai-provider", headers=headers).json()
    assert admin_view["active_provider"] == "yandex"
    assert admin_view["source"] == "default"

    # Смена модели по умолчанию личный выбор не трогает.
    client.put("/integrations/ai-provider", json={"active_provider": "claude"}, headers=headers)
    client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)
    assert client.get("/integrations/ai-provider", headers=user_headers).json()["source"] == "user"

    # Сброс — обратно к модели по умолчанию.
    reset = client.put("/auth/me/ai-provider", json={"ai_provider": None}, headers=user_headers)
    assert reset.json()["active_provider"] == "yandex"
    assert reset.json()["source"] == "default"
    assert client.get("/auth/me", headers=user_headers).json()["ai_provider"] is None

    # На ненастроенную модель — нельзя.
    client.patch("/integrations/routerai", json={"api_key": ""}, headers=headers)
    denied = client.put("/auth/me/ai-provider", json={"ai_provider": "claude"}, headers=user_headers)
    assert denied.status_code == 400


def test_request_context_carries_the_user_into_the_dispatcher(client, admin_token, monkeypatch):
    """`run_structured` глубоко в сервисах не получает пользователя — он берётся из контекста,
    который выставляет middleware. Проверяем сквозь реальный HTTP-запрос: эндпоинт ИИ-сводки
    аналитики должен уйти к клиенту, выбранному именно этим пользователем."""

    from app.services import analytics_service

    headers = _auth_headers(admin_token)
    _reset(client, headers)
    client.patch("/integrations/yandex-ai-studio", json={"api_key": "AQVNkey", "folder_id": "b1g"}, headers=headers)
    client.patch("/integrations/routerai", json={"api_key": "sk-abcd"}, headers=headers)
    client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)

    seen: list[str] = []

    def fake_run_structured(db, **kwargs):
        seen.append(ai_provider_service.get_active_provider(db))
        raise RuntimeError("stop here")

    monkeypatch.setattr(analytics_service, "run_structured", fake_run_structured)

    user_headers = _auth_headers(_create_regular_user_token(client, admin_token))
    client.put("/auth/me/ai-provider", json={"ai_provider": "claude"}, headers=user_headers)

    for hdrs in (user_headers, headers):
        resp = client.post("/analytics/ai-summary", json={}, headers=hdrs)
        assert resp.status_code != 401, resp.text

    assert seen == ["claude", "yandex"]


def test_job_context_uses_the_authors_choice(db_session):
    from app.services.ai_context import acting_as

    ai_provider_service._get_or_create(db_session).active_provider = "yandex"
    db_session.flush()
    author = db_session.query(User).first()
    author.ai_provider = "claude"
    db_session.flush()
    # Ключа RouterAI в этой сессии может не быть — тогда личный выбор откатывается к
    # умолчанию; фиксируем ключ, чтобы проверять именно контекст.
    ai_provider_service._get_or_create(db_session).routerai_api_key = "sk-test"
    db_session.flush()

    assert ai_provider_service.get_active_provider(db_session) == "yandex"
    with acting_as(author.id):
        assert ai_provider_service.get_active_provider(db_session) == "claude"
    assert ai_provider_service.get_active_provider(db_session) == "yandex"
    db_session.rollback()


def test_dispatcher_calls_only_the_active_client(db_session, monkeypatch):
    calls: list[str] = []

    def fake_yandex(db, **kwargs):
        calls.append("yandex")
        return _Answer(text="yandex")

    def fake_claude(db, **kwargs):
        calls.append("claude")
        return _Answer(text="claude")

    monkeypatch.setattr(ai_client.yandex_ai_client, "run_structured", fake_yandex)
    monkeypatch.setattr(ai_client.routerai_client, "run_structured", fake_claude)

    for provider in ("claude", "yandex", "claude"):
        monkeypatch.setattr(ai_client, "get_active_provider", lambda db, p=provider: p)
        answer = ai_client.run_structured(
            db_session, system_prompt="s", user_text="u", response_model=_Answer
        )
        assert answer.text == provider

    assert calls == ["claude", "yandex", "claude"]


def test_strict_schema_closes_objects_and_drops_titles():
    from app.services.routerai_client import _strict_schema

    class Inner(pydantic.BaseModel):
        severity: str

    class Outer(pydantic.BaseModel):
        items: list[Inner]
        score: int

    schema = _strict_schema(Outer.model_json_schema())
    assert schema["additionalProperties"] is False
    assert "title" not in schema
    inner = schema["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert "title" not in inner
    assert schema["required"] == ["items", "score"]


def test_coerce_shape_unwraps_bare_list_and_stringified_field():
    """Claude через RouterAI на схеме с единственным полем-списком отдаёт то голый массив,
    то JSON-строку в поле (18.09.2026). Обе формы приводятся к схеме; на схемах с несколькими
    полями ничего не трогается."""

    from app.services.routerai_client import _coerce_shape

    class Item(pydantic.BaseModel):
        text: str

    class Wrapper(pydantic.BaseModel):
        items: list[Item]

    class Two(pydantic.BaseModel):
        summary: str
        participate: bool

    assert _coerce_shape('[{"text": "a"}]', Wrapper) == {"items": [{"text": "a"}]}
    assert _coerce_shape('{"items": "[{\\"text\\": \\"a\\"}]"}', Wrapper) == {"items": [{"text": "a"}]}
    assert _coerce_shape('{"items": [{"text": "a"}]}', Wrapper) == {"items": [{"text": "a"}]}
    assert _coerce_shape('{"summary": "s", "participate": true}', Two) == {
        "summary": "s",
        "participate": True,
    }
    # Строка в поле, которая не JSON, — отдаётся как есть, ошибку даст валидация.
    assert _coerce_shape('{"items": "не json"}', Wrapper) == {"items": "не json"}
