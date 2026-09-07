import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    case,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.source import Source


class TenderStatus(str, enum.Enum):
    COLLECTING_BIDS = "collecting_bids"
    EVALUATION = "evaluation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TenderType(str, enum.Enum):
    """Тип конкурса (Приложение E ТЗ). Определяется ИИ-модулем на Этапе 5 — на Этапе 2
    поле остаётся пустым."""

    SUPPLY_ONLY = "supply_only"
    COMPLEX = "complex"
    WORKS_ONLY = "works_only"
    REVERIFICATION = "reverification"
    OTHER = "other"


class TenderStage(str, enum.Enum):
    """Внутренний пайплайн работы МИРТЕК с тендером (раздел 5.6 ТЗ, решение 03.09.2026).

    Не путать с `TenderStatus`: тот описывает объективное состояние закупки на площадке
    («идёт приём заявок»), а этот — что с ней сделали мы («заявка подана»). Поля
    ортогональны и сводить их в одно нельзя — потеряется одно из двух.
    """

    AI_SELECTED = "ai_selected"
    UNDER_REVIEW = "under_review"
    APPLICATION_SUBMITTED = "application_submitted"
    WON = "won"
    LOST = "lost"
    REJECTED = "rejected"


class RelevanceStatus(str, enum.Enum):
    """Прежний трёхзначный статус релевантности. С 03.09.2026 в БД его нет — колонка
    заменена на `stage` (раздел 7 ТЗ). Enum остаётся, потому что значения продолжают
    ходить во внешнем REST API, выгрузке в Excel и Bitrix-интеграции: там это часть
    контракта с потребителями, которых мы ломать не вправе."""

    NEW = "new"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


# Соответствие старого статуса релевантности и нового этапа (раздел 7 ТЗ). Одностороннее:
# `stage` богаче, поэтому «заявка подана»/«выиграли» отображаются в `confirmed` — для
# внешнего потребителя это по-прежнему «релевантный, подтверждённый» тендер.
STAGE_TO_RELEVANCE: dict[str, str] = {
    TenderStage.AI_SELECTED.value: RelevanceStatus.NEW.value,
    TenderStage.UNDER_REVIEW.value: RelevanceStatus.CONFIRMED.value,
    TenderStage.APPLICATION_SUBMITTED.value: RelevanceStatus.CONFIRMED.value,
    TenderStage.WON.value: RelevanceStatus.CONFIRMED.value,
    TenderStage.LOST.value: RelevanceStatus.CONFIRMED.value,
    TenderStage.REJECTED.value: RelevanceStatus.REJECTED.value,
}

RELEVANCE_TO_STAGE: dict[str, str] = {
    RelevanceStatus.NEW.value: TenderStage.AI_SELECTED.value,
    RelevanceStatus.CONFIRMED.value: TenderStage.UNDER_REVIEW.value,
    RelevanceStatus.REJECTED.value: TenderStage.REJECTED.value,
}


class Tender(Base):
    """Тендер (раздел 7 ТЗ). Полная целевая схема создаётся уже на Этапе 2 (раздел 9 ТЗ,
    решение №11 — "архитектура не переделывается"), но поля, которые заполняют более поздние
    этапы (регион/ФО/ОКПД2/тип конкурса — Этап 5, комментарий ИИ — Этап 5-6), остаются
    nullable и пустыми до соответствующего этапа."""

    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_tenders_source_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    # `lazy="joined"` — карточка тендера всегда показывает имя источника (список тендеров,
    # экспорт и т.д.), это избавляет от N+1 запроса при листинге вместо точечной подгрузки.
    source: Mapped[Source] = relationship(lazy="joined")
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Единый реестровый номер закупки: у ЕИС и дублирующих её площадок он совпадает —
    # по нему ловятся дубли МЕЖДУ источниками, которых пара (source_id, external_id) не
    # видит (раздел 7 ТЗ, «Дедупликация между источниками»).
    registry_number: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    customer_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    customer_contact_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    customer_contact_phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_contact_email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    organizer_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    procurement_method: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="RUB")
    application_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    region_organizer_code: Mapped[str | None] = mapped_column(
        String(2), ForeignKey("regions.code"), nullable=True
    )
    region_delivery_code: Mapped[str | None] = mapped_column(
        String(2), ForeignKey("regions.code"), nullable=True
    )
    federal_district_code: Mapped[int | None] = mapped_column(
        ForeignKey("federal_districts.code"), nullable=True
    )
    okpd2_code: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    tender_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(
        String(30), nullable=False, default=TenderStage.AI_SELECTED.value, index=True
    )

    # Результат профиля релевантности (раздел 5.1.1 ТЗ). `None` — фильтр ещё не применялся;
    # это не то же самое, что `False` («проверили и не подходит»), и путать их нельзя:
    # первое означает «не знаем», второе — обоснованный отказ.
    passed_relevance_filter: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, index=True
    )
    # Какая именно группа сработала. Без этого поля нельзя ни объяснить пользователю, почему
    # тендер в выборке, ни понять, какую группу править, если выборка мусорная.
    matched_keyword_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_keyword_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Решение модели: «это правда наша закупка» (раздел 5.4 ТЗ). Второй слой поверх профиля
    # ключевых слов — тот отвечает лишь «есть ли нужные слова» и не отличает поставку
    # счётчиков от аренды помещения, где счётчики упомянуты в составе имущества.
    #
    # `None` — модель не смотрела. Это не `False` («посмотрела и отклонила»): сбой сети или
    # выключенная интеграция не должны прятать закупку из списка.
    ai_relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_relevance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_relevance_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_relevance_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Ответственный именно за этот тендер — отдельно от справочника «регион →
    # ответственный» (раздел 5.6 ТЗ): по региону назначается умолчание, но по конкретной
    # закупке работу могут передать другому человеку.
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @hybrid_property
    def relevance_status(self) -> str:
        """Прежний статус релевантности как вычисляемый алиас поверх `stage` (раздел 7 ТЗ).

        Колонки в БД больше нет, но поле отдаётся внешним REST API, выгрузкой в Excel и
        Bitrix-интеграцией — это внешний контракт. Свойство гибридное, а не обычное:
        фильтры и группировки (`Tender.relevance_status.in_([...])`,
        `group_by(Tender.relevance_status)`) в существующем коде обращаются к классу, а не
        к экземпляру, и должны продолжать работать на уровне SQL.
        """

        return STAGE_TO_RELEVANCE.get(self.stage, RelevanceStatus.NEW.value)

    @relevance_status.inplace.expression
    @classmethod
    def _relevance_status_expression(cls):
        return case(STAGE_TO_RELEVANCE, value=cls.stage, else_=RelevanceStatus.NEW.value)

    @relevance_status.inplace.setter
    def _relevance_status_setter(self, value: str) -> None:
        """Запись старого значения переводится в этап: `set_relevance` и правка карточки
        по-прежнему принимают `new`/`confirmed`/`rejected`.

        Уже поданная заявка (или закрытый исход) при «подтверждении релевантности» не
        откатывается назад в `under_review` — это была бы потеря состояния пайплайна.
        """

        target = RELEVANCE_TO_STAGE.get(value)
        if target is None:
            raise ValueError(f"Недопустимый статус релевантности: {value}")
        if (
            target == TenderStage.UNDER_REVIEW.value
            and self.stage
            in (
                TenderStage.APPLICATION_SUBMITTED.value,
                TenderStage.WON.value,
                TenderStage.LOST.value,
            )
        ):
            return
        self.stage = target
