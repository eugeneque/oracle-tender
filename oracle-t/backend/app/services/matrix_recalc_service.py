"""Массовый пересчёт матрицы соответствия по уже разобранным закупкам (15.10.2026).

Матрица и итог «проходит / не проходит» сохраняются в момент расчёта. Когда меняются правила
подсчёта (разделы 60–62 ARCHITECTURE.md: позиции лота, ТТ/ТН, эталон ТЗ, модель под другим
названием), старые закупки остаются со старыми итогами, пока их не пересчитать. Раньше это
делалось по одной закупке кнопкой «Разобрать закупку заново» в карточке — на 24 закупки из
сверки это полчаса кликов. Здесь администратор ставит в очередь все закупки с матрицей разом.

Два режима:

* `evaluation` — только матрица по уже извлечённым требованиям («Расчёт соответствия»).
  Подхватывает новые правила подсчёта; дешевле — модель сверяет требования с каталогом,
  документацию заново не читает.
* `review` — полный разбор: требования извлекаются заново (с позициями лота), затем матрица
  и AI-оценка. Нужен, чтобы закупки, разобранные до 15.10.2026, получили позиции лота.

Стоимость оценивается по фактическому расходу (`ai_usage_records`) за последние 60 дней, а не
«на глаз»: средний расход одной задачи этого вида × число закупок.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.jobs import _TENDER_AI_KINDS, enqueue
from app.models.ai_usage import AiUsageRecord
from app.models.analysis import WinPercentage
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.tender import Tender
from app.models.user import User

MODES: dict[str, JobKind] = {
    "evaluation": JobKind.TENDER_EVALUATION,
    "review": JobKind.TENDER_FULL_REVIEW,
}
SCOPES = ("open", "all")
SAMPLE_DAYS = 60


class RecalcError(ValueError):
    """Неизвестный режим или охват — ошибка запроса, текст уходит пользователю."""


@dataclass
class PerTender:
    """Средний расход одной задачи по истории. `sample` — по скольким задачам посчитано;
    0 — истории нет, оценки нет (в интерфейсе — «появится после первых разборов»)."""

    sample: int
    tokens: int | None
    cost: float | None
    minutes: float | None


def _check(mode: str, scope: str) -> None:
    if mode not in MODES:
        raise RecalcError("Режим пересчёта: «evaluation» (только матрица) или «review» (полный разбор)")
    if scope not in SCOPES:
        raise RecalcError("Охват пересчёта: «open» (идёт приём заявок) или «all» (все с матрицей)")


def scope_tender_ids(db: Session, scope: str) -> list:
    """Закупки, у которых уже есть матрица (текущий расчёт процента). «open» — ещё и приём
    заявок не закончился или срок не указан: по прошедшим закупкам итог ничего не решает."""

    query = (
        select(Tender.id)
        .where(
            Tender.id.in_(select(WinPercentage.tender_id).where(WinPercentage.is_current.is_(True)))
        )
        .order_by(Tender.application_end.asc().nulls_last())
    )
    if scope == "open":
        query = query.where(
            or_(Tender.application_end.is_(None), Tender.application_end >= datetime.now(timezone.utc))
        )
    return list(db.scalars(query))


def _busy_tender_ids(db: Session, tender_ids: list) -> set:
    """Закупки, по которым разбор ИИ уже стоит в очереди или идёт: второй не ставится
    (`jobs.enqueue` вернёт идущий), и считать их в «поставлено» было бы неправдой."""

    if not tender_ids:
        return set()
    return set(
        db.scalars(
            select(BackgroundJob.tender_id).where(
                BackgroundJob.tender_id.in_(tender_ids),
                BackgroundJob.kind.in_(list(_TENDER_AI_KINDS)),
                BackgroundJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            )
        )
    )


def per_tender(db: Session, mode: str) -> PerTender:
    """Средний расход одной задачи. Для «только матрица» берутся обращения шага «Матрица
    соответствия» любой задачи (отдельно матрицу запускают редко, а внутри полного разбора
    она та же); для полного разбора — все обращения задач «Полный разбор закупки»."""

    since = datetime.now(timezone.utc) - timedelta(days=SAMPLE_DAYS)
    condition = [AiUsageRecord.created_at >= since, AiUsageRecord.job_id.is_not(None)]
    if mode == "evaluation":
        condition.append(AiUsageRecord.operation == "ComplianceResult")
    else:
        condition.append(AiUsageRecord.job_kind == JobKind.TENDER_FULL_REVIEW.value)

    per_job = (
        select(
            AiUsageRecord.job_id,
            func.sum(AiUsageRecord.prompt_tokens + AiUsageRecord.completion_tokens).label("tokens"),
            func.sum(AiUsageRecord.cost).label("cost"),
        )
        .where(*condition)
        .group_by(AiUsageRecord.job_id)
        .subquery()
    )
    row = db.execute(
        select(func.count(), func.avg(per_job.c.tokens), func.avg(per_job.c.cost)).select_from(per_job)
    ).one()
    sample, tokens, cost = row

    kind = MODES[mode].value
    seconds = db.scalar(
        select(func.avg(func.extract("epoch", BackgroundJob.finished_at - BackgroundJob.started_at))).where(
            BackgroundJob.kind == kind,
            BackgroundJob.status == JobStatus.SUCCESS.value,
            BackgroundJob.started_at.is_not(None),
            BackgroundJob.finished_at.is_not(None),
            BackgroundJob.created_at >= since,
        )
    )
    return PerTender(
        sample=int(sample or 0),
        tokens=int(tokens) if tokens is not None else None,
        cost=round(float(cost), 2) if cost is not None else None,
        minutes=round(float(seconds) / 60, 1) if seconds is not None else None,
    )


def estimate(db: Session, mode: str, scope: str) -> dict:
    _check(mode, scope)
    tender_ids = scope_tender_ids(db, scope)
    busy = _busy_tender_ids(db, tender_ids)
    count = len(tender_ids) - len(busy)
    average = per_tender(db, mode)
    parallel = max(1, get_settings().jobs_max_parallel)
    return {
        "mode": mode,
        "scope": scope,
        "tenders": count,
        "already_running": len(busy),
        "parallel": parallel,
        "per_tender": {
            "sample": average.sample,
            "tokens": average.tokens,
            "cost": average.cost,
            "minutes": average.minutes,
        },
        "total": {
            "tokens": average.tokens * count if average.tokens is not None else None,
            "cost": round(average.cost * count, 2) if average.cost is not None else None,
            # Задачи идут параллельно по `jobs_max_parallel` — общее время делится на них.
            "minutes": round(average.minutes * count / parallel) if average.minutes is not None else None,
        },
    }


def launch(db: Session, mode: str, scope: str, actor: User | None) -> dict:
    """Ставит задачи в очередь. Уже идущие разборы не дублируются (`jobs.enqueue`)."""

    _check(mode, scope)
    tender_ids = scope_tender_ids(db, scope)
    busy = _busy_tender_ids(db, tender_ids)
    queued = 0
    for tender_id in tender_ids:
        if tender_id in busy:
            continue
        tender = db.get(Tender, tender_id)
        if tender is None:
            continue
        enqueue(db, kind=MODES[mode], tender=tender, actor=actor)
        queued += 1
    return {"queued": queued, "already_running": len(busy)}
