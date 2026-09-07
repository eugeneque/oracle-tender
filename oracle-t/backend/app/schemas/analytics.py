"""Схемы ответов дашборда (раздел 5.6 ТЗ, «Аналитика»)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AnalyticsSummaryOut(BaseModel):
    total: int
    total_amount: Decimal
    with_price: int
    average_amount: Decimal
    deadline_soon: int
    analysed: int
    # Главная метрика с 03.09.2026 (раздел 5.5.1 ТЗ). Процент соответствия характеристик
    # остаётся рядом — это другой вопрос, а не устаревшая версия того же.
    average_ai_score: Decimal | None
    average_win_percentage: Decimal | None
    by_status: dict[str, int]
    by_relevance: dict[str, int]
    by_type: dict[str, int]


class MonthPointOut(BaseModel):
    month: date
    count: int
    amount: Decimal
    average_ai_score: Decimal | None
    average_win_percentage: Decimal | None


class RegionRowOut(BaseModel):
    code: str | None
    name: str
    federal_district: str | None
    count: int
    amount: Decimal


class CustomerRowOut(BaseModel):
    name: str
    count: int
    amount: Decimal


class LabelledPercentageOut(BaseModel):
    label: str
    percentage: Decimal | None
    count: int


class WinBreakdownOut(BaseModel):
    by_region: list[LabelledPercentageOut]
    by_type: list[LabelledPercentageOut]


class WidgetOut(BaseModel):
    key: str
    title: str
    value: str
    caption: str
    tone: str
    progress: float | None


class AnalyticsOverviewOut(BaseModel):
    """Всё, что нужно дашборду, одним ответом.

    Именно одним, а не пятью запросами: дашборд открывается целиком, и пять параллельных
    round-trip'ов ради одной картинки дают заметное мерцание блоков при загрузке.
    """

    summary: AnalyticsSummaryOut
    monthly: list[MonthPointOut]
    regions: list[RegionRowOut]
    customers: list[CustomerRowOut]
    win_breakdown: WinBreakdownOut
    widgets: list[WidgetOut]


class AiSummaryOut(BaseModel):
    generated_at: datetime
    headline: str | None
    highlights: list[str]
    risks: list[str]
    actions: list[str]
    error: str | None


class RegionResponsibleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    region_code: str
    region_name: str | None = None
    responsible_name: str | None
    manager_name: str | None
    updated_at: datetime


class RegionResponsibleUpdate(BaseModel):
    responsible_name: str | None = None
    manager_name: str | None = None
