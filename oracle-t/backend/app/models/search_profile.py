"""Профиль релевантности: что вообще считать «нашим» тендером (раздел 5.1.1 ТЗ).

Дешёвый текстовый фильтр ДО дорогого ИИ-анализа. Отвечает не «подходит ли прибор под ТЗ»
(это матрица соответствия, раздел 5.5.2) и не «стоит ли идти» (это AI-оценка, 5.5.1), а
«стоит ли вообще тратить на этот тендер вызов модели».

**Почему группы, а не один список слов.** Заказчику нужен один и тот же прибор в разных
качествах: купить, заменить, смонтировать, поверить, обслужить. Это разные типы закупки, у
них разные формулировки и разные ветки ОКПД2 — «Поверка» живёт в 71.12.40.x, а «Поставка» в
26.51.63.x. Слитый в кучу список слов не даст ни настроить охват по одному типу, ни понять
потом, почему тендер попал в выборку.

**Почему у каждой группы есть исключения.** «Поверка счётчиков» без запрета на воду, газ,
тепло и медицину собирает поверку чего угодно. Именно рабочие группы (замена, монтаж,
поверка, ремонт) ловят чужие сферы, поэтому исключения — часть группы, а не общая настройка.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KeywordMatchMode(str, enum.Enum):
    """Как трактуется набор ключей внутри группы.

    `any` — достаточно одного совпадения (обычный случай: синонимы одного и того же).
    `all` — нужны все ключи (для групп, где смысл рождается только из сочетания).
    """

    ANY = "any"
    ALL = "all"


class SearchProfile(Base):
    """Настройки охвата на уровне компании (раздел 5.1.1 ТЗ)."""

    __tablename__ = "search_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=False
    )
    # Прямой ответ на вопрос с созвона «есть ли привязка к сумме»: есть, настраиваемая.
    min_nmck: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True, default=Decimal("100000")
    )
    ai_score_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True, default=Decimal("60")
    )
    # Пусто или null — вся РФ. Отдельного признака «без ограничений» нет намеренно: он
    # неизбежно рассогласовался бы с самим списком.
    regions_scope: Mapped[list | None] = mapped_column(ARRAY(String(2)), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SearchKeywordGroup(Base):
    """Одна потребность заказчика: поставка, замена, поверка, обслуживание и т. д."""

    __tablename__ = "search_keyword_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    search_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Список строк с синтаксисом раздела 5.1.1: `слово*` — по основе,
    # `(a* b*)~N` — слова в пределах N слов друг от друга.
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Отрицательные ключи: встретилось — группа не срабатывает, даже если совпали
    # положительные.
    exclusion_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Коды ОКПД2 именно этой группы. Между группами одного производителя не совпадают:
    # поверка и поставка — разные ветки классификатора.
    okpd2_codes: Mapped[list | None] = mapped_column(ARRAY(String(20)), nullable=True)
    match_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default=KeywordMatchMode.ANY.value
    )
    # Ключевые слова, которыми группа ходит в поиск площадок. Отличаются от `keywords`:
    # у площадок нет ни стемминга, ни близости — им нужна обычная фраза. Пусто — группа
    # участвует в фильтрации уже собранного, но сама запросов не порождает.
    search_queries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
