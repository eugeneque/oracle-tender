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

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    # Баллы записи ГИСП по ПП 719 (миграция 0085, совещание 28.10.2026): для закупок годится
    # запись со 113 баллами и выше — одна и та же модель бывает в реестре и с 80 баллами.
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Запись заведена автоматическим опросом реестра (`registry_polling`, 13.10.2026), а не
    # человеком. Ручная правка сбрасывает отметку — и опрос такую запись больше не трогает.
    auto_found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class PollMethod(str, enum.Enum):
    """Как получена выгрузка реестра: скачана сервером или загружена человеком файлом
    (если реестр не отдал её роботу)."""

    DOWNLOAD = "download"
    UPLOAD = "upload"


class PollStatus(str, enum.Enum):
    OK = "ok"
    ERROR = "error"


class AdmissionRegistryPoll(Base):
    """Опрос реестра допуска (миграция 0073, 13.10.2026): когда смотрели, откуда взяли
    выгрузку, сколько моделей в ней нашлось. Нужен карточке модели, чтобы «не проверялось»
    отличалось от «реестр опрошен вчера, модели в нём не нашлось», и администратору — чтобы
    видеть, что автоматическая сверка не сломалась молча."""

    __tablename__ = "admission_registry_polls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    registry: Mapped[str] = mapped_column(String(40), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Строки, относящиеся к счётчикам электроэнергии наших производителей.
    rows_relevant: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    products_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    products_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
