import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderProtocolResults(Base):
    """Итоги процедуры, извлечённые из протоколов (11.11.2026, раздел 96 ARCHITECTURE.md).

    Перечень Bitrix24 «Что еще нужно от ИИ», пп. 69–87: вид и итоговость протокола, участники с
    ИНН, их цены, допуск или отклонение с причиной, победитель, результат нашей заявки,
    противоречия между протоколами. Заменяются целиком при каждом разборе; `documents` —
    адреса разобранных протоколов: по ним мониторинг понимает, что появился новый протокол.
    `{"documents": [...], "protocols": [...], "participants": [...], "lots": [...]}`.
    Разбор — `services/protocol_results_service.py`.
    """

    __tablename__ = "tender_protocol_results"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
