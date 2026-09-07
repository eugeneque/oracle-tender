from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FederalDistrict(Base):
    """Справочник федеральных округов (Приложение G ТЗ)."""

    __tablename__ = "federal_districts"

    code: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Region(Base):
    """Справочник регионов РФ (Приложение H ТЗ). Код — как на автомобильных номерах,
    двухзначная строка (включая ведущий ноль, например "01")."""

    __tablename__ = "regions"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    federal_district_code: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("federal_districts.code"), nullable=False
    )


class RegionResponsible(Base):
    """Ответственный и его руководитель по региону (раздел 5.6 ТЗ; поля Приложения D
    «Ответственный за регион» и «Руководитель ответственный за регион»).

    Отдельная таблица, а не колонки в `Region`: справочник регионов сидируется из
    Приложения H и переписывается при пересиде, а назначения вводит заказчик — их терять
    нельзя. Обе фамилии nullable: регион может быть заведён без назначения, и выгрузка
    должна отдавать пустую ячейку, а не падать.
    """

    __tablename__ = "region_responsibles"

    region_code: Mapped[str] = mapped_column(
        String(2), ForeignKey("regions.code"), primary_key=True
    )
    responsible_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manager_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
