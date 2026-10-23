"""Оценка специалистом вердикта матрицы соответствия (23.10.2026, раздел 70 ARCHITECTURE.md).

Ячейка матрицы — «требование × производитель». Специалист соглашается с вердиктом или
исправляет его: указывает верный статус, причину и пишет, в чём ошибка. Исправление делает
две вещи:

1. Перекрывает вердикт модели в этой закупке — в ячейке, в проценте и в итоге «проходит /
   не проходит» — и переживает «Пересчёт матрицы» (`compliance_service.evaluate_tender`).
2. Становится прецедентом: при разборе других закупок похожие требования уходят модели
   вместе с исправлением (`compliance_review_service.precedents_block`). Дообучать саму
   модель не на чем и незачем — требования повторяются из закупки в закупку, и пример
   исправления в запросе даёт тот же эффект сразу.

Формулировка требования хранится снимком: повторный анализ документации удаляет требования
(`tender_analysis._replace_requirements`), ссылка обнуляется, а исправление по тексту
находит новое требование той же закупки и остаётся прецедентом для остальных.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewKind(str, enum.Enum):
    AGREE = "agree"
    DISAGREE = "disagree"


class ReviewReason(str, enum.Enum):
    """Причина ошибки — решает, кому исправление пригодится как прецедент: неверно понятое
    требование одинаково для всех производителей, а неверный факт — только для своего."""

    FACT = "fact"  # неверный или устаревший факт о приборе: каталог, список ПО, реестр
    REQUIREMENT = "requirement"  # неверно понято требование
    MODEL = "model"  # сравнили не с той моделью или не с тем типом прибора
    OTHER = "other"


class ComplianceVerdictReview(Base):
    __tablename__ = "compliance_verdict_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("manufacturers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Верный статус по мнению специалиста — только у несогласия.
    corrected_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Снимок требования: формулировка ТЗ и ключ сопоставления (текст без регистра и лишних
    # пробелов плюс позиция спецификации) — по нему исправление находит требование заново.
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Что сказала модель, когда специалист ответил: без этого не видно, что именно исправлено.
    model_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    # Действует ли оценка: новое исправление ячейки гасит прежние, отзыв гасит своё.
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
