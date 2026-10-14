"""Журнал ошибок обращений к моделям ИИ (14.10.2026)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.log import Log, LogLevel
from app.services import ai_client
from app.services.ai_context import running_job
from app.services.ai_log_service import COMPONENT
from app.services import routerai_client
from tests.test_ai_usage import Answer, _job_with_tender, _ok_response, routerai  # noqa: F401


def _ai_logs(db_session, marker: str) -> list[Log]:
    return list(
        db_session.scalars(
            select(Log)
            .where(Log.component == COMPONENT, Log.details.contains(marker))
            .order_by(Log.timestamp)
        )
    )


def test_failed_attempts_go_to_ai_log_with_model_step_and_tender(db_session, routerai):  # noqa: F811
    """Каждая неудачная попытка — строка журнала ИИ уровнем WARNING: модель, шаг, номер
    попытки и закупка из фоновой задачи. Успешная попытка в журнал ошибок не пишется."""

    job, tender = _job_with_tender(db_session)
    routerai["responses"] = [
        httpx.ConnectError("нет связи"),
        _ok_response("не JSON", prompt_tokens=500, completion_tokens=10),
        _ok_response(prompt_tokens=500, completion_tokens=12),
    ]

    with running_job(datetime.now(timezone.utc), job.id):
        routerai_client.run_structured(
            db_session, system_prompt="s", user_text="u", response_model=Answer
        )

    first, second = _ai_logs(db_session, tender.external_id)
    assert first.level == LogLevel.WARNING and first.result == "error"
    assert first.action == "Сбой запроса к модели"
    assert "anthropic/claude-opus-5" in first.details
    assert "Попытка: 1 из 3" in first.details
    assert "нет связи" in first.details
    assert second.result == "invalid" and "Попытка: 2 из 3" in second.details


def test_final_failure_is_logged_as_error_and_reraised(db_session, monkeypatch):
    """Итоговый отказ `run_structured` — ERROR в журнале ИИ, исключение уходит вызывающему
    как раньше: сервис сам решает, как изолировать сбой."""

    marker = f"шлюз недоступен {uuid.uuid4().hex[:8]}"

    def broken(db, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(ai_client, "_run_structured", broken)
    monkeypatch.setattr(ai_client, "active_model", lambda db: ("deepseek", "deepseek/deepseek-v4"))

    with pytest.raises(RuntimeError):
        ai_client.run_structured(db_session, system_prompt="s", user_text="u", response_model=Answer)

    (entry,) = _ai_logs(db_session, marker)
    assert entry.level == LogLevel.ERROR
    assert entry.action == "Запрос к модели не выполнен"
    assert "deepseek/deepseek-v4" in entry.details
