"""AI-оценка по профилю: History / Task / Competencies (раздел 5.5.1 ТЗ, 03.09.2026).

Главная метрика тендера: отвечает не на вопрос «какой прибор подходит» (это матрица
соответствия, раздел 5.5.2), а на вопрос «стоит ли МИРТЕК идти в эту закупку и с какими
рисками». Три измерения считаются независимо и каждое обязано быть прослеживаемым до
источника — отсюда поля `*_evidence` рядом с каждым числом и комментарием.

История пересчётов не перезаписывается: новая оценка добавляется строкой, прежняя
помечается `is_current = false`. Иначе после изменения профиля компании нельзя объяснить,
почему вчера в списке было 82%, а сегодня 61%.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Verdict(str, enum.Enum):
    """Вердикт блока «Резюме» (раздел 5.5.1 ТЗ). В интерфейсе показывается заглавными:
    ИДТИ / ИДТИ С ОГОВОРКАМИ / НЕ ИДТИ."""

    GO = "go"
    GO_WITH_RESERVATIONS = "go_with_reservations"
    NO_GO = "no_go"


VERDICT_LABELS: dict[str, str] = {
    Verdict.GO.value: "ИДТИ",
    Verdict.GO_WITH_RESERVATIONS.value: "ИДТИ С ОГОВОРКАМИ",
    Verdict.NO_GO.value: "НЕ ИДТИ",
}


class WeakPointSeverity(str, enum.Enum):
    """Значимость слабого места. Отдельное структурированное поле, а не приставка к тексту:
    по нему сортируют и фильтруют (раздел 5.5.1 ТЗ)."""

    SIGNIFICANT = "significant"
    MODERATE = "moderate"
    MINOR = "minor"


SEVERITY_LABELS: dict[str, str] = {
    WeakPointSeverity.SIGNIFICANT.value: "значительно",
    WeakPointSeverity.MODERATE.value: "умеренно",
    WeakPointSeverity.MINOR.value: "незначительно",
}

# Порядок для сортировки списка слабых мест: сначала то, что может стоить участия.
SEVERITY_ORDER: dict[str, int] = {
    WeakPointSeverity.SIGNIFICANT.value: 0,
    WeakPointSeverity.MODERATE.value: 1,
    WeakPointSeverity.MINOR.value: 2,
}


class EvidenceType(str, enum.Enum):
    """На что может ссылаться обоснование числа (раздел 5.5.1 ТЗ, «Прослеживаемость»)."""

    REQUIREMENT = "requirement"
    TENDER_DOCUMENT = "tender_document"
    COMPANY_PROFILE_FIELD = "company_profile_field"
    SIMILAR_TENDER = "similar_tender"
    TENDER_FIELD = "tender_field"
    # Строка `company_participations` — реальный протокол участия МИРТЕК в похожей закупке.
    # Основная опора History после уточнения 03.09.2026 (раздел 5.5.1 ТЗ).
    COMPANY_PARTICIPATION = "company_participation"


class AiProfileScore(Base):
    __tablename__ = "ai_profile_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # History остаётся null, пока по похожим закупкам нет ни одной записи участия: ноль
    # означал бы «мы проверили и опыта нет», а правда — «данных нет вовсе» (раздел 5.5.1 ТЗ).
    history_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    history_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_evidence: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    task_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    task_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    competencies_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    competencies_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    competencies_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # [{severity, text}] — см. WeakPointSeverity.
    weak_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # {verdict, price, first_step} — три подпункта фиксированного формата (раздел 5.5.1 ТЗ).
    recommended_strategy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Копия профиля компании на момент расчёта: без неё старые оценки нельзя ни объяснить,
    # ни проверить по `*_evidence` после того, как профиль поправили.
    company_profile_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
