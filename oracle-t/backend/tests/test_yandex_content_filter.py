"""Отказ YandexGPT по фильтру тематики (28.09.2026, закупка 32616398561): вместо JSON —
«Я не могу обсуждать эту тему…» со статусом CONTENT_FILTER. Повтор бесполезен; клиент
пробует деловую формулировку, затем запасную модель Yandex AI Studio."""

from __future__ import annotations

from types import SimpleNamespace

import pydantic
import pytest

from app.services import yandex_ai_client as client
from app.services.tender_service import _REGISTRY_NUMBER_RE


class Answer(pydantic.BaseModel):
    fit: str


REFUSAL = "Я не могу обсуждать эту тему. Давайте поговорим о чём-нибудь ещё."


class _FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    def configure(self, **kwargs):
        return self

    def run(self, messages, timeout):
        self.prompts.append((messages[0]["text"], messages[1]["text"]))
        text, status = self.replies.pop(0)
        return [SimpleNamespace(text=text, status=SimpleNamespace(name=status))]


@pytest.fixture()
def fake_sdk(monkeypatch):
    holder = {}

    class _Models:
        def completions(self, name):
            return holder["model"]

    class _Studio:
        def __init__(self, **kwargs):
            self.models = _Models()

    import yandex_ai_studio_sdk

    monkeypatch.setattr(yandex_ai_studio_sdk, "AIStudio", _Studio)
    monkeypatch.setattr(client, "get_credentials", lambda db: ("key", "folder"))
    return holder


def test_refusal_is_not_retried_and_softened_prompt_is_tried(fake_sdk):
    model = _FakeModel(
        [(REFUSAL, "CONTENT_FILTER"), ('{"fit": "not_fit"}', "FINAL")]
    )
    fake_sdk["model"] = model
    client.drain_fallback_notes()

    answer = client.run_structured(
        None,
        system_prompt="Вынеси заключение",
        user_text="Лицензия ФСБ на разработку шифровальных (криптографических) средств",
        response_model=Answer,
    )

    assert answer.fit == "not_fit"
    # Два обращения: отказ (без повторов) и деловая формулировка.
    assert len(model.prompts) == 2
    system, user = model.prompts[1]
    assert system.startswith("Контекст работы: деловой анализ")
    assert "криптограф" not in user and "средств защиты информации" in user
    assert client.drain_fallback_notes()


def test_second_refusal_goes_to_fallback_model(fake_sdk, monkeypatch):
    fake_sdk["model"] = _FakeModel([(REFUSAL, "CONTENT_FILTER"), (REFUSAL, "CONTENT_FILTER")])
    calls = []

    def _fallback(**kwargs):
        calls.append(kwargs["model_name"])
        return Answer(fit="fit")

    monkeypatch.setattr(client, "_run_openai_compatible", _fallback)
    client.drain_fallback_notes()

    answer = client.run_structured(
        None, system_prompt="s", user_text="u", response_model=Answer
    )

    assert answer.fit == "fit"
    assert calls == [client.FILTER_FALLBACK_MODEL]
    assert any("запасная модель" in note for note in client.drain_fallback_notes())


def test_refusal_recognised_by_text_without_status():
    assert client.is_refusal(REFUSAL)
    assert not client.is_refusal('{"fit": "fit"}')


@pytest.mark.parametrize(
    "number, expected",
    [("32616398561", True), ("0373100078826000188", True), ("RH24092600069", False), ("12345678901", False)],
)
def test_registry_number_recognises_223_fz(number, expected):
    """Номер 223-ФЗ (11 цифр с тройкой) — тоже реестровый: иначе закупка с Росэлторга
    ложилась в список второй строкой рядом с той же закупкой из ЕИС."""

    assert bool(_REGISTRY_NUMBER_RE.match(number)) is expected


def test_routerai_quota_falls_back_to_yandex(monkeypatch):
    """Исчерпанный лимит RouterAI (28.09.2026, DeepSeek — системная модель): фоновые вызовы
    без автора не падают, а уходят в YandexGPT, если он подключён."""

    from app.services import ai_client
    from app.services.routerai_client import RouterAiQuotaError

    monkeypatch.setattr(ai_client, "get_active_provider", lambda db: "deepseek")
    monkeypatch.setattr(ai_client, "is_provider_configured", lambda db, provider: True)
    # Отключение моделей (29.09.2026) здесь не проверяется — все включены.
    monkeypatch.setattr(ai_client, "is_provider_enabled", lambda db, provider: True)
    monkeypatch.setattr(ai_client, "disabled_since", lambda db, provider: None)

    def _quota(*args, **kwargs):
        raise RouterAiQuotaError("spending limit exceeded")

    monkeypatch.setattr(ai_client.routerai_client, "run_structured", _quota)
    monkeypatch.setattr(
        ai_client.yandex_ai_client, "run_structured", lambda db, **kwargs: Answer(fit="fit")
    )
    client.drain_fallback_notes()

    assert ai_client.run_structured(None, system_prompt="s", user_text="u", response_model=Answer).fit == "fit"
    assert any("YandexGPT" in note for note in client.drain_fallback_notes())

    # YandexGPT не подключён — ошибка лимита поднимается как была.
    monkeypatch.setattr(ai_client, "is_provider_configured", lambda db, provider: False)
    with pytest.raises(RouterAiQuotaError):
        ai_client.run_structured(None, system_prompt="s", user_text="u", response_model=Answer)
