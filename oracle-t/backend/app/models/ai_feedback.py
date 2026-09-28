"""Ответ тендерного специалиста на заключение ИИ (28.09.2026).

Заключение ИИ — не справка, а позиция, с которой специалист соглашается или спорит. Спор
не должен оставаться устным: замечание сохраняется, уходит модели на пересмотр, и рядом
хранится, каким заключение было до замечания и каким стало после. Без этой пары нельзя ни
проверить, учла ли модель возражение, ни собрать из замечаний материал для правки промптов.

Замечания видят все — это общий архив по закупке, а не личные заметки.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FeedbackKind(str, enum.Enum):
    AGREE = "agree"
    DISAGREE = "disagree"


class FeedbackStatus(str, enum.Enum):
    # Согласие ничего не пересчитывает — оно просто записано.
    RECORDED = "recorded"
    # Несогласие ждёт пересмотра, пересматривается, учтено или пересмотр не удался.
    PENDING = "pending"
    PROCESSING = "processing"
    APPLIED = "applied"
    ERROR = "error"


class AiScoreFeedback(Base):
    __tablename__ = "ai_score_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Заключение до и после — снимками, а не только ссылками на `ai_profile_scores`: оценки
    # версионируются, но раздел «Ответы специалистов» должен читаться без сборки истории.
    score_before_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_profile_scores.id", ondelete="SET NULL"), nullable=True
    )
    score_after_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_profile_scores.id", ondelete="SET NULL"), nullable=True
    )
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Что модель ответила на замечание: с чем согласилась, что изменила, где осталась при своём.
    ai_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
