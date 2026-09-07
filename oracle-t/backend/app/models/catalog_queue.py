"""Очередь задач на пополнение справочника продукции из внешних источников
(ФГИС и сайт производителя) — раздел 5.1.3, 5.3, 5.9 ТЗ.

**Зачем отдельная таблица, а не `background_jobs`.** `BackgroundJob` привязана к тендеру
(`tender_id`) и обслуживает разовые действия пользователя над карточкой закупки. Здесь
объект другой — модель прибора или код СИ, — и работа приходит из двух источников сразу:

* **по расписанию** — периодическая ревалидация уже сохранённых карточек: не истёк ли срок
  действия свидетельства об утверждении типа, не вышла ли новая редакция «Описания типа»,
  цел ли номер в Госреестре;
* **по событию** — модуль сопоставления встретил модель без данных в справочнике; ждать
  следующего суточного цикла в этом случае нельзя, запрос ставится в очередь немедленно.

Оба триггера кладут запись в одну и ту же таблицу и обрабатываются одним и тем же кодом —
иначе логика запроса к ФГИС разъехалась бы на две копии (п.1.1 задания).

Состояние живёт в БД, а не в памяти процесса, по той же причине, что и у `BackgroundJob`:
после перезапуска сервера незавершённые запросы должны быть видны, а не исчезнуть.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CatalogQueueReason(str, enum.Enum):
    """Почему запись оказалась в очереди. Различать важно не для отчётности: у ревалидации
    и у события разная срочность и разная реакция на пустой результат (для ревалидации
    «ничего не нашлось» — тревожный сигнал о том, что тип исчез из реестра, для события —
    штатный исход «прибора нет в Госреестре»)."""

    SCHEDULED_REVALIDATION = "scheduled_revalidation"
    MISSING_CATALOG_DATA = "missing_catalog_data"
    MANUAL = "manual"


class CatalogQueueStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    # Отдельный терминальный статус, а не разновидность ошибки: обработка прошла успешно,
    # но результат нельзя принять автоматически (несколько кандидатов в реестре либо
    # кандидат не того вида измерений). Такие записи — рабочий список для человека.
    NEEDS_REVIEW = "needs_review"


class CatalogLookupTask(Base):
    """Один запрос к внешнему источнику данных о продукции."""

    __tablename__ = "catalog_lookup_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Ключ адаптера-исполнителя (`fgis`, `mirtek_site`) — тот же идентификатор, что в
    # `sources.adapter_key`, чтобы очередь и планировщик говорили об источнике одинаково.
    adapter_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CatalogQueueStatus.QUEUED.value, index=True
    )
    manufacturer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    si_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("si_types.id", ondelete="CASCADE"), nullable=True
    )
    # Что искать. Хранится строкой, а не только ссылкой на `Product`: по событию из модуля
    # сопоставления в очередь попадает и модель конкурента, которой в справочнике ещё нет.
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Рассмотренные кандидаты реестра при неоднозначности — чтобы человек видел, из чего
    # выбирала система, не повторяя поиск руками.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
