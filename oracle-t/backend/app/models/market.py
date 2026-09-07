"""Похожие тендеры, эмбеддинги и статистика по нише (раздел 7 ТЗ, решение 03.09.2026).

Три сущности разной гранулярности, которые легко перепутать:

* `TenderEmbedding` — вектор одного тендера, по нему считается близость;
* `SimilarTender` — пара «тендер ↔ похожий тендер» с косинусной близостью, вкладка
  «Похожие» и вход измерения History (раздел 5.5.1);
* `NicheStatistics` — агрегат ПО НИШЕ (ОКПД2 + регион), вкладка «Расчёт». Это не исход
  конкретной закупки (`TenderOutcome`) и в History не подставляется: там нужны отдельные
  похожие тендеры, а не средняя температура по нише.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderEmbedding(Base):
    """Вектор тендера (раздел 5.5.1 ТЗ).

    Хранится в JSONB списком чисел, а не типом `vector`: расширение `pgvector` в текущем
    образе БД (`postgres:15`) и в локальной установке отсутствует, а миграция с
    `CREATE EXTENSION vector` уронила бы и разработку, и тесты. Косинусная близость на
    нынешнем объёме (тысячи тендеров) считается в Python за один проход — см.
    `app/services/similarity_service.py`. Переход на `pgvector` с ANN-индексом остаётся
    отдельной задачей уровня инфраструктуры (сменить образ БД → миграция типа колонки),
    формат хранения при этом меняется, а вызывающий код — нет.
    """

    __tablename__ = "tender_embeddings"

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list] = mapped_column(JSONB, nullable=False)
    # Векторы разных моделей несравнимы между собой: при смене модели старые строки надо
    # пересчитать, а до пересчёта — не смешивать с новыми.
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SimilarTender(Base):
    __tablename__ = "similar_tenders"
    __table_args__ = (
        UniqueConstraint("tender_id", "similar_tender_id", name="uq_similar_tenders_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    similar_tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    similarity_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NicheSource(str, enum.Enum):
    """Откуда взят агрегат. По решению 03.09.2026 в первую очередь — парсинг готовых
    агрегатов с OPTI, а не пересчёт из собственных `tender_outcomes`."""

    OPTIHUB_PARSED = "optihub_parsed"
    COMPUTED_FROM_TENDER_OUTCOMES = "computed_from_tender_outcomes"


class NicheStatistics(Base):
    __tablename__ = "niche_statistics"
    __table_args__ = (
        UniqueConstraint("okpd2_code", "region_code", name="uq_niche_statistics_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    okpd2_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    region_code: Mapped[str | None] = mapped_column(
        String(2), ForeignKey("regions.code"), nullable=True
    )
    # Сколько закупок легло в основу агрегата. Показывается рядом с числами: агрегат по
    # семи закупкам и по полутора тысячам выглядят одинаково, а стоят разного.
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_participants: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    single_participant_share: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    median_price_reduction_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    usual_submission_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{manufacturer_name, wins_count, win_share_pct, typical_discount_pct}]
    top_winners: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
