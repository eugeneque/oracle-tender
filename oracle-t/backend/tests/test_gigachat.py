"""GigaChat (Сбер, 08.10.2026) — задел четвёртого провайдера ИИ-модуля.

Живой API не зовём (ключа у заказчика ещё нет): проверяется обвязка — ключ хранится
зашифрованным и наружу не уходит, без ключа модель нельзя выбрать, диспетчер отдаёт запрос
клиенту GigaChat, а клиент берёт токен один раз и разбирает ответ-вызов функции.
"""

import httpx
import pydantic
import pytest

from app.models.integration_setting import AiProviderSettings
from app.services import ai_client, ai_provider_service, gigachat_client


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clean_gigachat(client, admin_token):
    """Настройки — синглтон, тестовая БД не пересоздаётся: ключ GigaChat после теста
    стирается, иначе он поменял бы `configured_providers` в соседних тестах."""

    headers = _headers(admin_token)
    reset = {"auth_key": "", "scope": "", "model": "", "base_url": ""}
    client.patch("/integrations/gigachat", json=reset, headers=headers)
    client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)
    gigachat_client.reset_token_cache()
    yield headers
    client.put("/integrations/ai-provider", json={"active_provider": "yandex"}, headers=headers)
    client.patch("/integrations/gigachat", json=reset, headers=headers)
    gigachat_client.reset_token_cache()


class _Answer(pydantic.BaseModel):
    text: str


def test_unconfigured_gigachat_is_listed_but_not_selectable(client, clean_gigachat):
    headers = clean_gigachat
    settings = client.get("/integrations/gigachat", headers=headers).json()
    assert settings["is_configured"] is False
    assert settings["scope"] == ai_provider_service.DEFAULT_GIGACHAT_SCOPE
    assert settings["model"] == ai_provider_service.DEFAULT_GIGACHAT_MODEL

    switched = client.put(
        "/integrations/ai-provider", json={"active_provider": "gigachat"}, headers=headers
    )
    assert switched.status_code == 400
    assert "GigaChat" in switched.json()["detail"]


def test_auth_key_is_encrypted_and_masked(client, clean_gigachat, db_session):
    headers = clean_gigachat
    saved = client.patch(
        "/integrations/gigachat",
        json={"auth_key": "Y2xpZW50OnNlY3JldA-tail", "scope": "gigachat_api_corp"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["is_configured"] is True
    assert body["scope"] == "GIGACHAT_API_CORP"
    assert "Y2xpZW50OnNlY3JldA-tail" not in saved.text

    row = db_session.get(AiProviderSettings, ai_provider_service._SINGLETON_ID)
    db_session.refresh(row)
    assert row.gigachat_auth_key_encrypted != "Y2xpZW50OnNlY3JldA-tail"
    assert ai_provider_service.get_gigachat_credentials(db_session)[0] == "Y2xpZW50OnNlY3JldA-tail"

    bad = client.patch("/integrations/gigachat", json={"scope": "GIGACHAT_API_FOO"}, headers=headers)
    assert bad.status_code == 400

    status = client.put(
        "/integrations/ai-provider", json={"active_provider": "gigachat"}, headers=headers
    ).json()
    assert status["active_provider"] == "gigachat"
    assert status["label"] == "GigaChat"
    assert status["model"] == ai_provider_service.DEFAULT_GIGACHAT_MODEL


def test_dispatch_goes_to_gigachat_client(client, clean_gigachat, db_session, monkeypatch):
    client.patch("/integrations/gigachat", json={"auth_key": "key"}, headers=clean_gigachat)
    seen: list[str] = []

    def fake(db, **kwargs):
        seen.append(kwargs["user_text"])
        return _Answer(text="ok")

    monkeypatch.setattr(ai_client.gigachat_client, "run_structured", fake)
    monkeypatch.setattr(
        ai_client.routerai_client, "run_structured", lambda *a, **k: pytest.fail("не тот клиент")
    )
    monkeypatch.setattr(ai_client, "get_active_provider", lambda db: "gigachat")
    assert ai_client.run_structured(
        db_session, system_prompt="s", user_text="u", response_model=_Answer
    ).text == "ok"
    assert seen == ["u"]
    assert ai_client.active_model(db_session) == ("gigachat", ai_provider_service.DEFAULT_GIGACHAT_MODEL)


def test_client_caches_token_and_reads_function_call(client, clean_gigachat, db_session, monkeypatch):
    client.patch("/integrations/gigachat", json={"auth_key": "key"}, headers=clean_gigachat)
    calls: list[tuple[str, dict]] = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        request = httpx.Request("POST", url)
        if url == gigachat_client.OAUTH_URL:
            assert kwargs["headers"]["Authorization"] == "Basic key"
            assert kwargs["data"] == {"scope": ai_provider_service.DEFAULT_GIGACHAT_SCOPE}
            return httpx.Response(
                200, json={"access_token": "tok", "expires_at": 4_102_444_800_000}, request=request
            )
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        body = kwargs["json"]
        assert body["function_call"] == {"name": "_Answer"}
        assert "temperature" not in body  # нулевая температура — через top_p
        return httpx.Response(
            200,
            json={
                "model": "GigaChat-2-Max",
                "choices": [
                    {
                        "finish_reason": "function_call",
                        "message": {"function_call": {"name": "_Answer", "arguments": {"text": "да"}}},
                    }
                ],
            },
            request=request,
        )

    monkeypatch.setattr(gigachat_client.httpx, "post", fake_post)
    for _ in range(2):
        answer = gigachat_client.run_structured(
            db_session, system_prompt="s", user_text="u", response_model=_Answer
        )
        assert answer.text == "да"
    assert [url for url, _ in calls].count(gigachat_client.OAUTH_URL) == 1


def test_payment_required_is_quota_error(client, clean_gigachat, db_session, monkeypatch):
    client.patch("/integrations/gigachat", json={"auth_key": "key"}, headers=clean_gigachat)

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        if url == gigachat_client.OAUTH_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_at": 4_102_444_800_000}, request=request)
        return httpx.Response(402, json={"message": "Payment Required"}, request=request)

    monkeypatch.setattr(gigachat_client.httpx, "post", fake_post)
    with pytest.raises(ai_provider_service.AiQuotaExceededError):
        gigachat_client.run_structured(db_session, system_prompt="s", user_text="u", response_model=_Answer)
