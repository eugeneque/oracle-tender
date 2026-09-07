import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderCard(Base):
    """Полная карточка закупки с сайта источника (раздел 5.6 ТЗ).

    Хранится как JSON, а не разложенной по колонкам: состав разделов у ЕИС отличается между
    44-ФЗ и 223-ФЗ, между способами закупки и между редакциями формы. Любая жёсткая схема
    здесь означала бы миграцию на каждое изменение формы и молчаливую потерю полей, которых
    мы не предусмотрели, — а показать пользователю нужно ровно то, что он видит на сайте.

    Отдельная таблица, а не колонка в `tenders`: карточка весит десятки килобайт и нужна
    только при открытии одного тендера, а список тендеров тянет свои строки сотнями.
    """

    __tablename__ = "tender_cards"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    # {"sections": [...], "tables": {...}, "tab_urls": {...}} — см. app/adapters/eis_card.py
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
