import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobKind(str, enum.Enum):
    """Что именно выполняется в фоне. Все виды — долгие обращения к YandexGPT по всей
    документации тендера (разделы 5.4, 5.5 ТЗ): на крупном тендере это десятки секунд,
    и держать на них открытый HTTP-запрос из браузера нельзя."""

    TENDER_ANALYSIS = "tender_analysis"
    TENDER_EVALUATION = "tender_evaluation"
    # AI-оценка по профилю (раздел 5.5.1 ТЗ): три вызова модели подряд — два измерения и
    # текстовый блок «Резюме», плюс разбор девяти разделов «Дополнительно» перед ними.
    AI_PROFILE_SCORE = "ai_profile_score"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class BackgroundJob(Base):
    """Фоновая задача анализа/расчёта соответствия (раздел 5.9 ТЗ — отказоустойчивость).

    Состояние живёт в БД, а не в памяти процесса: пользователь должен видеть, что запуск
    идёт, даже если он перезагрузил страницу или открыл карточку с другого компьютера, а
    после перезапуска сервера незавершённые задачи должны быть видны как оборванные, а не
    исчезнуть бесследно."""

    __tablename__ = "background_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED.value, index=True
    )
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Сколько раз задача уже выполнялась: при ошибке она перезапускается автоматически
    # (раздел 5.9 ТЗ — «повторные попытки при ошибке ИИ-анализа»).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
