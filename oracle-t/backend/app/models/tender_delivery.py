import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderDeliveryTerms(Base):
    """Модель поставки и исполнение договора, извлечённые из документации (05.11.2026).

    Bitrix24 1.7.0 принимает в разделе `contract` четыре новых типа строк: `supply_model`,
    `delivery_point` (грузополучатель + адрес), `delivery_schedule` (этап графика) и
    `delivery_request_terms` (поставка по заявкам). Данные небольшие и заменяются целиком при
    каждом разборе, поэтому хранятся одним JSONB-документом, а не четырьмя таблицами:
    `{"supply_model": {...}|null, "points": [...], "stages": [...], "requests": {...}|null}`.
    Разбор — `services/delivery_terms_service.py`.
    """

    __tablename__ = "tender_delivery_terms"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
