"""Какой моделью строится матрица соответствия (12.11.2026, раздел 104 ARCHITECTURE.md).

Матрица — 89% всех токенов ИИ. Выбор модели для неё — отдельно от модели разбора и от
личного выбора: администратор может перевести на дешёвую модель только матрицу и сравнить
качество, не трогая остальное. `None` — как раньше, матрица идёт моделью всего разбора.

Выбранная модель закрепляется за обращениями матрицы так же, как авторазбор
(`ai_context.forcing_provider`): выключенную её не подменяют другой — запрос откажет, а не
потратит токены чужой модели.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.ai_usage import AiUsageRecord
from app.models.integration_setting import AiProviderSettings
from app.models.log import LogLevel
from app.models.user import User
from app.services import ai_provider_service
from app.services.ai_provider_service import PROVIDER_LABELS
from app.services.audit import log_action

SAMPLE_DAYS = 60


def get(db: Session) -> str | None:
    value = db.execute(select(AiProviderSettings.matrix_provider)).scalar_one_or_none()
    return value if value in PROVIDER_LABELS else None


def costs(db: Session) -> list[dict]:
    """Средний расход матрицы на одну закупку по моделям за 60 дней — чтобы выбор делался
    по цифрам, а не «на глаз». Цена YandexGPT — по тарифу (`ai_usage_service.yandex_cost`)."""

    since = datetime.now(timezone.utc) - timedelta(days=SAMPLE_DAYS)
    rows = db.execute(
        select(
            AiUsageRecord.provider,
            func.count(distinct(AiUsageRecord.tender_id)),
            func.sum(AiUsageRecord.prompt_tokens + AiUsageRecord.completion_tokens),
            func.sum(AiUsageRecord.cost),
        )
        .where(
            AiUsageRecord.operation == "ComplianceResult",
            AiUsageRecord.created_at >= since,
            AiUsageRecord.tender_id.is_not(None),
        )
        .group_by(AiUsageRecord.provider)
    ).all()
    result = []
    for provider, tenders, tokens, cost in rows:
        if provider not in PROVIDER_LABELS or not tenders:
            continue
        result.append(
            {
                "provider": provider,
                "tenders": int(tenders),
                "tokens_per_tender": int((tokens or 0) / tenders),
                "cost_per_tender": round(float(cost) / tenders, 2) if cost is not None else None,
            }
        )
    return sorted(result, key=lambda item: item["provider"])


def status(db: Session) -> dict:
    return {"provider": get(db), "costs": costs(db)}


def set_provider(db: Session, provider: str | None, *, actor: User) -> dict:
    if provider is not None and provider not in PROVIDER_LABELS:
        raise ValueError(f"Неизвестная модель: {provider}")
    settings = ai_provider_service._get_or_create(db)  # noqa: SLF001 - синглтон настроек моделей
    settings.matrix_provider = provider
    settings.updated_by_id = actor.id
    log_action(
        db,
        component="ai_models",
        action="matrix_provider",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"Матрица соответствия: {PROVIDER_LABELS[provider]}"
            if provider
            else "Матрица соответствия: модель всего разбора"
        ),
        user_id=actor.id,
    )
    db.commit()
    return status(db)
