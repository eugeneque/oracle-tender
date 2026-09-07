"""Дашборд «Аналитика» (раздел 5.6 ТЗ).

Фильтры принимаются те же, что и в списке тендеров, — дашборд обязан показывать цифры по
тому же срезу, который пользователь выбрал в списке, иначе разойдутся и объяснить это
будет нечем. Набор параметров сокращён до тех, которыми реально режут аналитику
(период, источник, регион, ФО, тип, статус, ОКПД2): фильтровать дашборд по проценту
победителя бессмысленно — этот процент сам является предметом анализа.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.analytics import AiSummaryOut, AnalyticsOverviewOut
from app.services.analytics_service import (
    generate_ai_summary,
    get_by_region,
    get_monthly_series,
    get_summary,
    get_top_customers,
    get_widgets,
    get_win_breakdown,
)
from app.services.tender_service import TenderFilters

router = APIRouter(prefix="/analytics", tags=["analytics"])


def analytics_filters(  # noqa: PLR0913 - по параметру на фильтр, как и в списке тендеров
    publish_date_from: date | None = None,
    publish_date_to: date | None = None,
    source: list[str] | None = Query(default=None),
    region: list[str] | None = Query(default=None),
    federal_district: list[int] | None = Query(default=None),
    tender_type: list[str] | None = Query(default=None),
    tender_status: list[str] | None = Query(default=None),
    relevance_status: list[str] | None = Query(default=None),
    okpd2: str | None = Query(default=None, max_length=20),
) -> TenderFilters:
    return TenderFilters(
        publish_date_from=publish_date_from,
        publish_date_to=publish_date_to,
        source_keys=source or [],
        region_codes=region or [],
        federal_district_codes=federal_district or [],
        tender_types=tender_type or [],
        statuses=tender_status or [],
        relevance_statuses=relevance_status or [],
        okpd2_prefix=okpd2,
    )


@router.get("/overview", response_model=AnalyticsOverviewOut)
def get_overview(
    months: int = Query(default=12, ge=3, le=36),
    filters: TenderFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    return {
        "summary": get_summary(db, filters),
        "monthly": get_monthly_series(db, filters, months=months),
        "regions": get_by_region(db, filters),
        "customers": get_top_customers(db, filters),
        "win_breakdown": get_win_breakdown(db, filters),
        "widgets": get_widgets(db),
    }


@router.post("/ai-summary", response_model=AiSummaryOut)
def post_ai_summary(
    filters: TenderFilters = Depends(analytics_filters),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Текстовая сводка по всем разделам силами YandexGPT.

    POST, а не GET: каждый вызов — это обращение к платной модели, и такой запрос не должен
    случайно повторяться при обновлении страницы или уходить в кэш браузера.
    """

    return generate_ai_summary(db, filters, user)
