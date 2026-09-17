"""Записи о допуске модели прибора в реестрах, которых требуют заказчики (замечание
тестировщика 16.09.2026): реестр российской промышленной продукции (ПП РФ № 719, ГИСП
Минпромторга), заключение аттестационной комиссии ПАО «Россети» (ЗАК) и реестр
российского ПО Минцифры — для встроенного ПО прибора.

Это не характеристики прибора, а его **допуск**: класс точности у модели один и навсегда,
а запись в реестре имеет номер, дату и срок действия, после которого прибор формально
перестаёт подходить под ТЗ, где написано «действующее ЗАК». Поэтому отдельная таблица, а
не строки в `product_characteristics`: у записи есть даты, по которым код сам считает
«действует / истекает / истекла», и это состояние должно попадать в матрицу соответствия
детерминированно, а не по тому, как модель прочтёт строку «ЗАК до 2027».

Запись заводится вручную. Сами реестры для автоматической сверки закрыты: ГИСП отвечает
403 роботам, ezak.rosseti.ru — только из российских сетей; оба заведены в «Источники» с
проверкой доступности, и когда доступ появится, у записи уже есть поле `url` на карточку
в реестре и `verified_at` — когда человек сверял. Одна запись на пару «модель × реестр»:
при продлении обновляется номер и срок, история продлений задаче не нужна.

`presence = absent` — не «записи нет в нашей базе», а «проверено: в реестре этой модели
нет». Разница принципиальна для сопоставления: первое даёт `no_data`, второе —
`not_meets`. Незаполненное поле не должно превращаться в обвинение прибора.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AdmissionRegistry(str, enum.Enum):
    """Реестры допуска, о которых спрашивают ТЗ. Хранится строкой, как и остальные
    перечисления проекта: список расширяемый (у Газпрома свой реестр МТР, у РЖД — свой)."""

    # Реестр промышленной продукции, произведённой на территории РФ (ПП РФ № 719) — ГИСП.
    INDUSTRIAL_PRODUCTS = "industrial_products"
    # Заключение аттестационной комиссии ПАО «Россети» — реестр ЗАК (ezak.rosseti.ru).
    ROSSETI_ATTESTATION = "rosseti_attestation"
    # Единый реестр российских программ для ЭВМ и БД (Минцифры) — для встроенного ПО.
    SOFTWARE_REGISTRY = "software_registry"


class RegistryPresence(str, enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"


class ProductRegistryRecord(Base):
    __tablename__ = "product_registry_records"
    __table_args__ = (
        UniqueConstraint("product_id", "registry", name="uq_product_registry_records_product_registry"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    registry: Mapped[str] = mapped_column(String(40), nullable=False)
    presence: Mapped[str] = mapped_column(
        String(10), nullable=False, default=RegistryPresence.PRESENT.value
    )
    # Реестровый номер записи (у ГИСП — «№ записи», у Россетей — номер ЗАК, у Минцифры —
    # номер в реестре ПО). Пустой при `presence = absent`.
    record_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issued_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Срок действия. `None` — бессрочно либо неизвестен; «неизвестен» помечается в заметке.
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Ссылка на карточку записи в реестре — чтобы сверку можно было повторить.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    product = relationship("Product")
