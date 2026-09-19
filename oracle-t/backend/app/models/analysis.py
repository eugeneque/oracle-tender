"""Анализ тендера: требования, матрица соответствия, процент победителя (разделы 5.4, 5.5,
7 ТЗ — Этапы 5 и 6).

Три сущности одной цепочки: из документов тендера извлекаются `Requirement`, каждое
требование сопоставляется с продукцией каждого производителя (`ComplianceMatrixEntry`), из
статусов сопоставления считается `WinPercentage`. `TenderOutcome` к этой цепочке не
относится — это задел на вторую очередь методики (раздел 5.5 ТЗ), схема заводится сразу,
чтобы не переделывать её потом.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RequirementKind(str, enum.Enum):
    """К чему относится требование (18.09.2026).

    До этого извлекались только требования к товару, и закупки на обслуживание, монтаж и
    поверку — профильные для компании — оставались без единого требования. Вид решает,
    кто требование читает: матрица соответствия (раздел 5.5 ТЗ) — только `PRODUCT`,
    AI-оценка по профилю (раздел 5.5.1) — все три, измерение «Компетенции» опирается
    прежде всего на `PARTICIPANT`.
    """

    PRODUCT = "product"  # характеристики прибора, комплектация, документы на товар
    SERVICE = "service"  # состав и объём работ/услуг, сроки, гарантия на работы, приёмка
    PARTICIPANT = "participant"  # допуски, лицензии, стаж, персонал участника


class Criticality(str, enum.Enum):
    """Критичность требования (раздел 5.4 ТЗ). Веса для формулы процента победителя —
    в `app/services/compliance_service.py`."""

    CRITICAL = "critical"
    IMPORTANT = "important"
    MINOR = "minor"


class ComplianceStatus(str, enum.Enum):
    MEETS = "meets"
    PARTIAL = "partial"
    NOT_MEETS = "not_meets"
    NO_DATA = "no_data"


class ComplianceSource(str, enum.Enum):
    """Откуда получен вердикт — соответствует трём шагам сопоставления раздела 5.5 ТЗ плюс
    отдельная пометка для семантического сравнения силами модели."""

    SI_TYPE = "si_type"
    PRODUCT_CATALOG = "product_catalog"
    USER_MANUAL_FALLBACK = "user_manual_fallback"
    AI_SEMANTIC = "ai_semantic"
    # Список поддерживаемого оборудования ПО верхнего уровня (Пирамида, Энергосфера и др.)
    # — четвёртый источник фактов, для требований об интеграции (16.09.2026).
    UPPER_SOFTWARE = "upper_software"
    # Запись о допуске в реестре (ПП 719/ГИСП, ЗАК Россетей, реестр ПО) — пятый источник,
    # для требований о допуске; факт детерминированный, из `product_registry_records`.
    ADMISSION_REGISTRY = "admission_registry"


class Requirement(Base):
    """Требование тендера (раздел 5.4 ТЗ, п.1-3).

    Хранится и исходная формулировка (`text` — то, что написано в документации; её видит
    человек при проверке), и нормализованная (`normalized_text` — пригодная для
    сопоставления с полями каталога). Разделение принципиально: сравнивать надо
    нормализованное, а показывать и оспаривать — исходное.
    """

    __tablename__ = "requirements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id"), nullable=False, index=True
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tender_documents.id"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    criticality: Mapped[str] = mapped_column(
        String(20), nullable=False, default=Criticality.IMPORTANT.value
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RequirementKind.PRODUCT.value,
        server_default=RequirementKind.PRODUCT.value,
    )
    # Группа характеристик Приложения C, если требование удалось к ней отнести, — по ней
    # сопоставление знает, где искать ответ в каталоге.
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Поле Приложения C внутри группы. Заполняется, когда требование удалось привязать к
    # конкретной характеристике: тогда сравнение идёт детерминированно, без обращения к модели.
    field_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    verified_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComplianceMatrixEntry(Base):
    """Ячейка матрицы соответствия: вердикт по паре «требование × производитель»
    (раздел 5.5 ТЗ).

    Уникальность по тройке (требование, производитель, продукт) — повторный расчёт обновляет
    вердикт, а не плодит строки. `product_id` в ключе нужен потому, что у производителя
    несколько подходящих моделей, и матрица показывает лучшую из них, но пересчёт может
    выбрать другую.
    """

    __tablename__ = "compliance_matrix_entries"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id",
            "manufacturer_id",
            name="uq_compliance_requirement_manufacturer",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id"), nullable=False, index=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id"), nullable=False, index=True
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WinPercentage(Base):
    """Процент победителя по производителю (раздел 5.5 ТЗ, методика этапа 1).

    Это взвешенная оценка соответствия характеристик, а не вероятность победы: цена и история
    торгов в неё не входят. Формулировка обязательна к показу рядом с числом в интерфейсе —
    см. раздел 5.5 ТЗ.
    """

    __tablename__ = "win_percentages"
    __table_args__ = (
        # Частичный индекс вместо простой уникальности по паре: пересчёт не перезаписывает
        # строку, а добавляет новую и гасит прежнюю (`is_current = false`), — иначе после
        # пополнения каталога нельзя объяснить, почему процент изменился. «Текущая» запись
        # при этом ровно одна, и список не получает недетерминированный результат.
        Index(
            "uq_win_tender_manufacturer_current",
            "tender_id",
            "manufacturer_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id"), nullable=False, index=True
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id"), nullable=False, index=True
    )
    percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Сколько требований осталось без определённого статуса: процент считается только по
    # требованиям с известным вердиктом, и без этого числа он выглядит увереннее, чем есть.
    requirements_total: Mapped[int] = mapped_column(nullable=False, default=0)
    requirements_scored: Mapped[int] = mapped_column(nullable=False, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutcomeSource(str, enum.Enum):
    """Откуда взят исход закупки (раздел 7 ТЗ). `optihub_parsed` появился по решению
    03.09.2026 — данные берутся из уже агрегированного источника, а не собираются с нуля."""

    EIS_PROTOCOL = "eis_protocol"
    OPTIHUB_PARSED = "optihub_parsed"
    OTHER = "other"


class TenderOutcome(Base):
    """Итог состоявшейся закупки (раздел 5.5 ТЗ, методика этапа 2).

    Логикой пока не заполняется — таблица заводится заранее, чтобы накопление истории не
    потребовало миграции схемы, когда до второй очереди дойдут руки. `tender_id` nullable:
    протокол итогов может относиться к закупке, которой нет в нашей базе.
    """

    __tablename__ = "tender_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id"), nullable=True, index=True
    )
    external_tender_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    winner_manufacturer_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    winner_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    protocol_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default=OutcomeSource.OTHER.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
