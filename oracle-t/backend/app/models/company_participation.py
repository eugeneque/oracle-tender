"""История участия МИРТЕК в закупках (раздел 7 ТЗ, уточнение 03.09.2026).

Основной источник измерения History (раздел 5.5.1): вопрос «выигрывали ли мы такие тендеры
раньше» отвечается по внешним данным по ИНН, а не по самоотчёту компании. Записи приходят
двумя путями — выгрузкой из реестра контрактов ЕИС (`eis_contracts`) и ручным добавлением
того, что выгрузка не покрыла (`manual`).

**Важное свойство источника.** Реестр контрактов ЕИС содержит только заключённые контракты,
то есть исключительно победы: проигрышей и снятий с торгов в нём нет по устройству. Поэтому
`WINS_ONLY_SOURCES` помечает такие источники, а History отказывается считать по ним долю
побед — 100% по выборке, куда поражения не могли попасть, вводили бы в заблуждение ровно так
же, как ноль вместо «нет данных». Проигрыши добираются из протоколов итогов (отдельный
источник) или вручную.

Почему исходов три, а не два: `disqualified` (не допустили, обычно по формальным причинам)
хранится отдельно от `lost` (участвовали и проиграли по существу). Схлопнуть их в один
статус — значит записать в «проиграли по цене» случаи, где заявку вообще не рассматривали, и
получить искажённый вывод о шансах компании.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ParticipationOutcome(str, enum.Enum):
    WON = "won"
    LOST = "lost"
    DISQUALIFIED = "disqualified"
    UNKNOWN = "unknown"


OUTCOME_LABELS: dict[str, str] = {
    ParticipationOutcome.WON.value: "Победа",
    ParticipationOutcome.LOST.value: "Проигрыш",
    ParticipationOutcome.DISQUALIFIED.value: "Дисквалификация",
    ParticipationOutcome.UNKNOWN.value: "Исход неизвестен",
}


class ParticipationSource(str, enum.Enum):
    EIS_CONTRACTS = "eis_contracts"
    # Проигрыш, выведенный из пайплайна: заявку подавали (наша отметка), контракт достался
    # другому (факт из ЕИС). Отдельный источник, потому что это не выгрузка и не ручной ввод.
    EIS_RESULTS = "eis_results"
    MANUAL = "manual"


SOURCE_LABELS: dict[str, str] = {
    ParticipationSource.EIS_CONTRACTS.value: "Реестр контрактов ЕИС",
    ParticipationSource.EIS_RESULTS.value: "Итоги закупок ЕИС",
    ParticipationSource.MANUAL.value: "Вручную",
}

# Источники, которые по устройству отдают только победы. Выборка целиком из них не годится
# для расчёта доли побед — см. модульный докстринг и `ai_profile_service._history_dimension`.
WINS_ONLY_SOURCES: frozenset[str] = frozenset({ParticipationSource.EIS_CONTRACTS.value})


class CompanyParticipation(Base):
    __tablename__ = "company_participations"
    __table_args__ = (
        # Ключ дедупликации выгрузки: одна и та же закупка не должна удваиваться при
        # повторном запуске. Ручные записи внешнего идентификатора не имеют
        # (см. `external_tender_id` ниже) и под ограничение не попадают.
        UniqueConstraint(
            "manufacturer_id",
            "external_tender_id",
            name="uq_company_participations_external",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=False
    )
    # Сопоставление с нашей базой — не обязанность, а удача: история приходит по ИНН и
    # накрывает годы, когда система ещё не собирала тендеры.
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="SET NULL"), nullable=True
    )
    # Для ручных записей — NULL, а не пустая строка: иначе две ручные записи столкнутся на
    # уникальном ограничении, а в Postgres NULL'ы в UNIQUE не конфликтуют.
    external_tender_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    tender_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    customer_org_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    our_inn: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    our_bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    price_drop_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    competitors_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ParticipationOutcome.UNKNOWN.value, index=True
    )
    final_contract_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    executed_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # «Выводы/заметки» из формы: только руками, выгрузка это поле не трогает даже при
    # повторном импорте той же записи.
    lessons_learned_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ParticipationSource.MANUAL.value
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
