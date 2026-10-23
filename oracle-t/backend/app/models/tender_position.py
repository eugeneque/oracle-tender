import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderPosition(Base):
    """Позиция закупки — строка спецификации заказчика: что и сколько закупается (23.10.2026).

    Тендерный отдел смотрит таблицу, которую публикует заказчик («Счётчик однофазный — 120
    шт.»), а у нас до этого были только подписи позиций у требований (`Requirement.position`)
    без количества. Позиции извлекаются моделью из документации отдельным запросом
    (`services/tender_positions.py`) и при каждом разборе пересоздаются целиком. В Bitrix24
    уходят строками `procurement_position`.
    """

    __tablename__ = "tender_positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Порядок как в спецификации.
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Номер позиции и лота — как в документе («1», «1.2»); лотов у нас отдельно нет, по
    # умолчанию «1».
    position_no: Mapped[str | None] = mapped_column(String(30), nullable=True)
    lot_number: Mapped[str] = mapped_column(String(30), nullable=False, default="1")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Количество числом, если оно указано; «по заявкам», «в пределах суммы» — только в
    # `quantity_text`.
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    quantity_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
