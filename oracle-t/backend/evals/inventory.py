"""Инвентаризация разметки специалистов: сколько эталонных примеров есть по каждому слою.

    cd oracle-t/backend && POSTGRES_HOST=localhost .venv/bin/python -m evals.inventory

Только чтение. Решает, какие слои измерять первыми (PLAN.md, этап 0).
"""

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.ai_feedback import AiScoreFeedback
from app.models.compliance_review import ComplianceVerdictReview
from app.models.tender import Tender

HUMAN_YES = ("under_review", "application_submitted", "won", "lost")


def main() -> None:
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Tender))
        print(f"Закупок всего: {total}")

        print("\n[A/B] Решения по закупкам (stage):")
        for stage, n in db.execute(
            select(Tender.stage, func.count()).group_by(Tender.stage).order_by(func.count().desc())
        ):
            print(f"  {stage:24} {n}")
        print("  Отметка общих профилей (passed_relevance_filter):")
        for val, n in db.execute(
            select(Tender.passed_relevance_filter, func.count()).group_by(
                Tender.passed_relevance_filter
            )
        ):
            print(f"    {val!s:8} {n}")
        print("  Оценка модели (ai_relevant):")
        for val, n in db.execute(
            select(Tender.ai_relevant, func.count()).group_by(Tender.ai_relevant)
        ):
            print(f"    {val!s:8} {n}")
        marked = db.scalar(
            select(func.count()).select_from(Tender).where(
                Tender.relevance_marked_at.is_not(None)
            )
        )
        print(f"  Размечено человеком (relevance_marked_at): {marked}")
        print("  Из них отсеяно профилями (нужна аудит-выборка):",
              db.scalar(select(func.count()).select_from(Tender).where(
                  Tender.passed_relevance_filter.is_(False)
              )))

        print("\n[C] Оценки вердиктов матрицы (compliance_verdict_reviews, is_current):")
        for kind, reason, n in db.execute(
            select(
                ComplianceVerdictReview.kind, ComplianceVerdictReview.reason, func.count()
            )
            .where(ComplianceVerdictReview.is_current.is_(True))
            .group_by(ComplianceVerdictReview.kind, ComplianceVerdictReview.reason)
            .order_by(func.count().desc())
        ):
            print(f"  {kind:10} {reason or '-':14} {n}")

        print("\n[D] Ответы на заключение ИИ (ai_score_feedback):")
        for kind, status, n in db.execute(
            select(AiScoreFeedback.kind, AiScoreFeedback.status, func.count())
            .group_by(AiScoreFeedback.kind, AiScoreFeedback.status)
            .order_by(func.count().desc())
        ):
            print(f"  {kind:10} {status:12} {n}")


if __name__ == "__main__":
    main()
