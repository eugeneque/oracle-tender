"""Рынок счётчиков электроэнергии по поверкам ФГИС «Аршин» (ТЗ `ARSHIN_ANALYTICS.md`,
29.10.2026; раздел 79 ARCHITECTURE.md).

Отчёт, который аналитик раз в квартал собирала вручную (программа Чернова → Excel →
Power BI): сколько счётчиков ЭЭ каждого производителя поверено за период, в каких
исполнениях, с какими интерфейсами и сколько из них локализовано по ПП 719.

Устройство в три слоя:

* **сырые поверки** (`meter_verifications`) — только по типам счётчиков ЭЭ, по одной строке на
  `global_id` записи ФГИС (аннулированная остаётся с `annulled = true`: так видно, что именно
  отозвано, и повторная загрузка того же пакета ничего не удваивает);
* **справочник** — типы ГРСИ → модификации → варианты написания. Поверители пишут модификацию
  как придётся (латиница вместо кириллицы, 0 вместо О, лишние пробелы), поэтому сырое
  `mi_modification` — это вариант, а модификация с атрибутами отчёта (1ф/3ф, исполнение,
  интерфейс, локализация) — каноническая запись, к которой варианты привязываются;
* **помесячный агрегат** (`meter_verification_monthly`) «вариант × месяц → количество». Именно
  по варианту, а не по модификации: переразметка варианта не требует пересчёта миллионов
  строк — отчёт просто соединяет агрегат со справочником заново.

Ключи — целые, а не UUID, как в остальной схеме: на `meter_market_variants` и
`meter_market_orgs` ссылаются ~30 млн строк поверок, и 4 байта вместо 16 на каждой — это
гигабайты таблицы и индексов. Внешних ключей у `meter_verifications` нет по той же причине:
загрузка идёт пачками COPY, и проверка FK на каждой строке слепка удваивала бы её время;
целостность держит сам загрузчик (варианты и поверители заводятся до записи пачки).
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TypeReviewStatus(str, enum.Enum):
    """Решение по описанию типа: включать ли его в отчёт. Новые типы счётчиков ЭЭ Сова
    находит сама — по поверкам, которые приходят из ФГИС, — а интеллектуальный ли это
    счётчик по ПП 890, решает человек (раздел 6.1 ТЗ)."""

    NEW = "new"
    INCLUDED = "included"
    EXCLUDED = "excluded"


class MeterDesign(str, enum.Enum):
    """Исполнение прибора. «н/д» — отдельная категория, как в Power BI (раздел 11 ТЗ, п. 5)."""

    DIN = "din"
    CABINET = "cabinet"
    SPLIT = "split"
    UNKNOWN = "nd"


DESIGN_LABELS: dict[str, str] = {
    MeterDesign.DIN.value: "DIN-рейка",
    MeterDesign.CABINET.value: "Шкафное",
    MeterDesign.SPLIT.value: "Сплит",
    MeterDesign.UNKNOWN.value: "н/д",
}


class MappedBy(str, enum.Enum):
    """Откуда привязка варианта к модификации. Ручная привязка автоматикой не пересматривается."""

    IMPORT = "import"
    AUTO = "auto"
    MANUAL = "manual"


class LocalizationSource(str, enum.Enum):
    """Откуда значения локализации модификации. Ручное значение не затирается ни импортом,
    ни сопоставлением с ГИСП (раздел 6.5 ТЗ)."""

    IMPORT = "import"
    GISP = "gisp"
    MANUAL = "manual"


class LoadKind(str, enum.Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"


class LoadStatus(str, enum.Enum):
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class MeterMarketManufacturer(Base):
    """Производитель для отчёта — владелец описания типа (правило 1 ТЗ). Одно имя в отчёте,
    сколько бы написаний ни было в источниках (`ООО"МИРТЕК"` и `ООО "МИРТЕК"`)."""

    __tablename__ = "meter_market_manufacturers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Нормализованные ключи написаний (`meter_market_normalize.manufacturer_key`): по ним
    # производитель находится при импорте и при заведении нового типа из карточки ФГИС.
    synonyms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MeterMarketGroup(Base):
    """Группа компаний для экрана «Группы компаний». Производитель без группы показывается
    группой из одной компании — заводить такие группы руками не нужно."""

    __tablename__ = "meter_market_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)


class MeterMarketGroupMember(Base):
    """Компания в группе с периодом (годы включительно, `NULL` — без границы): АтомЦифроСбыт
    в 2022–2025 считается в «Энергомере», с 2026 — в «Заводе НАРТИС» (раздел 6.4 ТЗ)."""

    __tablename__ = "meter_market_group_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("meter_market_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    manufacturer_id: Mapped[int] = mapped_column(
        ForeignKey("meter_market_manufacturers.id", ondelete="CASCADE"), nullable=False
    )
    year_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MeterMarketType(Base):
    """Описание типа (номер ГРСИ). Заводится загрузчиком при первой поверке счётчика ЭЭ
    нового типа — со статусом «на решение» — или импортом справочника аналитика."""

    __tablename__ = "meter_market_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mit_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    notation: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer_id: Mapped[int | None] = mapped_column(
        ForeignKey("meter_market_manufacturers.id", ondelete="SET NULL"), nullable=True
    )
    cert_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    mpi_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_report: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TypeReviewStatus.NEW.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MeterMarketModification(Base):
    """Каноническая модификация с атрибутами отчёта."""

    __tablename__ = "meter_market_modifications"
    __table_args__ = (UniqueConstraint("type_id", "name", name="uq_meter_market_modifications_type_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(
        ForeignKey("meter_market_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # «1ф» / «3ф»; `NULL` — не размечено.
    phase: Mapped[str | None] = mapped_column(String(4), nullable=True)
    design: Mapped[str] = mapped_column(String(10), nullable=False, default=MeterDesign.UNKNOWN.value)
    # Интерфейс в едином написании (`normalize_interface`) — основа блока «Интерфейсы».
    interface: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mpi_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # «Дополнительно» аналитика — в отчёт не идёт.
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Производитель, если он отличается от владельца типа: ГРСИ 61891-15 в справочнике
    # записан и на МИРТЕК, и на «Умный учёт» (открытый вопрос, раздел 11 ТЗ).
    manufacturer_override_id: Mapped[int | None] = mapped_column(
        ForeignKey("meter_market_manufacturers.id", ondelete="SET NULL"), nullable=True
    )
    in_rpp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Дата внесения исполнения в РПП — точка отсчёта отчёта о локализации (правило 4 ТЗ).
    rpp_included_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    rpp_record_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    localization_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MeterMarketVariant(Base):
    """Сырое написание модификации в поверках (тип × `mi_modification`). Неразмеченный
    вариант (`modification_id IS NULL`) в отчёте виден строкой «не размечено», а не
    пропадает: иначе итоги молча разошлись бы с ФГИС (раздел 6.3 ТЗ)."""

    __tablename__ = "meter_market_variants"
    __table_args__ = (UniqueConstraint("type_id", "raw", name="uq_meter_market_variants_type_raw"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(
        ForeignKey("meter_market_types.id", ondelete="CASCADE"), nullable=False
    )
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    norm_key: Mapped[str] = mapped_column(Text, nullable=False)
    modification_id: Mapped[int | None] = mapped_column(
        ForeignKey("meter_market_modifications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mapped_by: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MeterMarketOrg(Base):
    """Организация-поверитель — словарь, чтобы строка поверки хранила номер, а не название."""

    __tablename__ = "meter_market_orgs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class MeterVerification(Base):
    """Поверка счётчика ЭЭ — одна строка на `global_id` записи ФГИС."""

    __tablename__ = "meter_verifications"

    global_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    variant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mi_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    annulled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MeterVerificationMonthly(Base):
    """Сколько пригодных неаннулированных поверок у варианта за месяц (правила 2 и 7 ТЗ)."""

    __tablename__ = "meter_verification_monthly"

    variant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class MeterMarketLoad(Base):
    """Пакет выгрузки ФГИС: слепок или файл изменений. Последний успешно применённый `id`
    — состояние цепочки; следующий пакет ищется в манифесте по `prevId`."""

    __tablename__ = "meter_market_loads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[str] = mapped_column(String(32), nullable=False)
    prev_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    md5: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False)
    # Последний полностью обработанный CSV внутри пакета: слепок — это база на 01.09.2022 и
    # все ежедневные изменения с тех пор, вложенные архивами (раздел 79 ARCHITECTURE.md), и
    # после обрыва разбор продолжается со следующего файла, а не с начала.
    checkpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rows_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_kept: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_annulled: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MeterMarketRppRecord(Base):
    """Запись РПП (ГИСП, ПП 719) по модификации. Записей у исполнения бывает несколько — с
    ростом баллов (83 → 95 → 113), и поверка относится к той, что действовала на дату поверки."""

    __tablename__ = "meter_market_rpp_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    modification_id: Mapped[int] = mapped_column(
        ForeignKey("meter_market_modifications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    record_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default=LocalizationSource.GISP.value)


class MeterMarketScoreThreshold(Base):
    """Порог баллов ПП 719 для «соответствия требованиям года» (правило 3 ТЗ). Настройка, а не
    константа: нижняя граница 2024 года и порог 2026 года у аналитика ещё не подтверждены."""

    __tablename__ = "meter_market_score_thresholds"

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    min_score: Mapped[float] = mapped_column(Float, nullable=False)
