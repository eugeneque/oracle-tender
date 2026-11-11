import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderContractConditions(Base):
    """Условия договора и обеспечения, извлечённые из документации (11.11.2026).

    Перечень Bitrix24 «Что еще нужно от ИИ» (пп. 4, 6, 16–27, 30–31, 95): срок действия и
    лимит договора, срок поставки с видом дней и событием-основанием, гарантия, оплата,
    аванс, постоплата, неустойки, обеспечение заявки и договора, возврат обеспечения, срок
    подписания, комиссия площадки, модель обязательств заказчика. Как и условия поставки
    (`tender_delivery_terms`), заменяются целиком при каждом разборе — один JSONB-документ:
    `{"conditions": [{"kind": ..., "value": ..., ...}, ...]}`.
    Разбор — `services/contract_conditions_service.py`.
    """

    __tablename__ = "tender_contract_conditions"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
