import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderEisRegistry(Base):
    """Сведения реестров ЕИС по закупке (19.11.2026, раздел 115 ARCHITECTURE.md).

    `complaints` — жалобы из реестра жалоб (пп. 41–47, 89), `contracts` — договоры из реестра
    договоров/контрактов с редакциями (пп. 90, 94), `customer_history` — закупки заказчика по ИНН
    за год (п. 7). Заменяются целиком; заполняет `eis_registry_service`."""

    __tablename__ = "tender_eis_registry"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    complaints: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    contracts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    customer_history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    registry_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    history_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenderGuarantee(Base):
    """Выданная банковская гарантия по закупке (п. 32): вводит тендерный отдел. Срок действия —
    то, с чем сравнивается сдвиг срока подачи или исполнения договора."""

    __tablename__ = "tender_guarantees"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # APPLICATION — обеспечение заявки, CONTRACT — исполнения договора, WARRANTY — гарантийных
    # обязательств.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank: Mapped[str | None] = mapped_column(String(300), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TenderMailMessage(Base):
    """Письмо площадки по закупке из почтового ящика (п. 65): запрос заказчика о дополнении или
    уточнении нашей заявки приходит только участнику. `kind`: `APPLICATION_REQUEST`."""

    __tablename__ = "tender_mail_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    sender: Mapped[str | None] = mapped_column(String(300), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
