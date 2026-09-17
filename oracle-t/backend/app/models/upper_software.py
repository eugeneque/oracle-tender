"""ПО верхнего уровня: списки поддерживаемого оборудования и их связь со справочником
(замечание тестировщика 16.09.2026).

Две таблицы:

* `upper_software_devices` — по строке на запись списка поддерживаемых устройств одной
  площадки (Пирамида, Энфорс, Энергосфера, яЭнергетик, АльфаЦЕНТР, Некта, ЛЭРС). Площадка
  — строка `sources` с типом `upper_software`: так списки видны и управляются в общем
  разделе «Источники», с расписанием и проверкой доступности, как ФГИС и сайты
  производителей. Запись хранит то, что написано на сайте (`device_raw`,
  `manufacturer_raw`), и результат разбора (`device_names`, `si_codes`) — человеку при
  проверке нужно видеть исходник, а не только то, что из него вычленила программа.
* `upper_software_product_links` — какие модели справочника закрыты этой записью и по
  какому признаку. Связь многие-ко-многим: одна строка «МИРТЕК-12-РУ» у Пирамиды покрывает
  все исполнения семейства, а одна модель встречается у семи площадок.

`manufacturer_id` на записи — кто из наших производителей за ней стоит. Разрешается
сервисом по номеру ГРСИ (самый надёжный признак), по названию производителя на сайте или
по бренду в обозначении прибора — у Энергосферы и ЛЭРС производитель на странице не
указан вовсе. `NULL` — производитель не из справочника (Landis+Gyr, Эльстер и прочие):
такие записи хранятся, чтобы список был полным, но в сопоставлении не участвуют.

`fingerprint` — ключ идемпотентности повторного обхода: площадка + раздел + строка как
на сайте. Пропавшие с сайта записи не удаляются, а перестают получать `last_seen_at` —
как модели в каталоге (п.2.4 задания); по этому же полю отличается «нет в списке» от
«список ещё не читали».
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UpperSoftwareDevice(Base):
    __tablename__ = "upper_software_devices"
    __table_args__ = (
        UniqueConstraint("source_id", "fingerprint", name="uq_upper_software_devices_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Раздел списка на сайте: у Пирамиды — таблица («ПО „Пирамида 2.0“ / „Пирамида-Сети“»,
    # «Контроллер SM-160»), у Энергосферы — секция таблицы («Счётчики электроэнергии СПОДЭС»).
    section: Mapped[str] = mapped_column(String(255), nullable=False)
    device_raw: Mapped[str] = mapped_column(Text, nullable=False)
    device_names: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    manufacturer_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    si_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    device_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # «Все счётчики по протоколу СПОДЭС» — поддержка по протоколу, а не по конкретной
    # модели. В сопоставлении такая запись даёт «частично», а не «соответствует».
    is_generic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Что ещё сказано о поддержке: флаги колонок Пирамиды, функции Энергосферы и
    # яЭнергетика, каналы связи Некты, примечания.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    manufacturer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # По какому признаку определён производитель: `si_code`, `manufacturer_name`,
    # `device_brand`. Человеку при проверке важно, насколько твёрдое это основание.
    manufacturer_matched_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UpperSoftwareProductLink(Base):
    __tablename__ = "upper_software_product_links"
    __table_args__ = (
        UniqueConstraint("device_id", "product_id", name="uq_upper_software_product_links_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("upper_software_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # `si_code` — совпал номер ГРСИ типа СИ модели; `designation` — обозначение из списка
    # является префиксом обозначения модели (или наоборот).
    matched_by: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
