import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AiUsageRecord(Base):
    """Одно обращение к модели ИИ: сколько токенов ушло и на что (14.10.2026).

    Заведено под вопрос руководства «сколько Сова тратит и сколько будет тратить»: прогноз
    расхода по оценке «на глаз» расходился в разы, а провайдеры возвращают точное число
    токенов в каждом ответе — его достаточно сохранить. Пишется каждая попытка, включая
    повторы и сбои: повтор тоже оплачивается, а доля сбоев — сама по себе показатель модели.

    `user_id`, `tender_id`, `job_id` — без внешних ключей: запись пишется отдельной сессией
    (`ai_usage_service.record_call`), и проверка ключа на строку, ещё не закоммиченную
    сессией вызывающего кода, ждала бы её транзакцию. К тому же учёт расхода должен
    переживать удаление закупки или пользователя."""

    __tablename__ = "ai_usage_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False, index=True
    )
    # "yandex" | "claude" | "deepseek" | "gigachat" — кто обслужил запрос (после подмены
    # при исчерпанном лимите — фактический, а не запрошенный).
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Схема ответа (`ComplianceResult`, `RequirementsResult`…) — по ней видно, какой шаг
    # разбора потратил токены; человеческое название — `ai_usage_service.OPERATION_LABELS`.
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    job_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tender_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Из входящих — взятые провайдером из кэша (у DeepSeek и Claude они в разы дешевле).
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Провайдер не вернул счётчик (сбой до ответа) — токены оценены по длине текста.
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # success | invalid (ответ не по схеме или оборван) | error (сеть, HTTP) | quota | filter
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Стоимость, если её вернул сам провайдер (шлюз RouterAI); иначе пусто — цены у моделей
    # разные и меняются, считать их по прайсу в коде значило бы показывать устаревшие числа.
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
