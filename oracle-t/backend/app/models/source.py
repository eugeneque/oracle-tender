import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceType(str, enum.Enum):
    """Тип источника (раздел 7 ТЗ). Расширяемый — хранится строкой, не нативным enum БД,
    по тому же принципу, что и `User.role` (см. app/models/user.py)."""

    EIS = "eis"
    ETP_FEDERAL_COMMERCIAL = "etp_federal_commercial"
    ETP_CORPORATE = "etp_corporate"
    # Источники справочника продукции, а не тендеров (раздел 4.2, 4.3, 5.3 ТЗ): реестр
    # утверждённых типов СИ и сайты производителей. Они живут в той же таблице, чтобы
    # быть видимыми и управляемыми в общем разделе «Источники» — с расписанием, статусом
    # и проверкой доступности, — но из опроса тендеров исключены (см.
    # `CATALOG_SOURCE_TYPES` ниже).
    FGIS = "fgis"
    MANUFACTURER_SITE = "manufacturer_site"
    # Списки поддерживаемого оборудования ПО верхнего уровня (Пирамида, Энергосфера и
    # др., замечание тестировщика 16.09.2026) — тоже источник справочника, а не тендеров:
    # по ним проверяется требование «интеграция в ПО верхнего уровня».
    UPPER_SOFTWARE = "upper_software"
    # Реестры допуска продукции (замечание тестировщика 16.09.2026): реестр российской
    # промышленной продукции (ГИСП, ПП РФ № 719) и реестр ЗАК ПАО «Россети». Данных они
    # роботам не отдают (403 и доступ только из российских сетей), поэтому записи о допуске
    # заводятся вручную (`product_registry_records`), а источник здесь — чтобы доступность
    # реестра была видна в мониторинге и на него была официальная ссылка для сверки.
    ADMISSION_REGISTRY = "admission_registry"
    # Заявки, заведённые вручную (решение 15.09.2026): заказчик сам прислал проект договора
    # или ТЗ с характеристиками приборов, и площадки у такой закупки нет. Одна системная
    # строка `MANUAL_SOURCE_KEY` на все заявки — `tenders.source_id` обязателен, а заводить
    # по источнику на заказчика значило бы плодить справочник из одноразовых записей.
    MANUAL = "manual"


class SourceStatus(str, enum.Enum):
    """Статус источника с точки зрения доступа/включённости в опрос (раздел 5.6 ТЗ,
    настраивается администратором)."""

    ACTIVE = "active"
    PENDING_ACCESS = "pending_access"
    DISABLED = "disabled"


class AdapterStatus(str, enum.Enum):
    """Готовность программного адаптера источника. Отдельная ось от `status`: `status`
    описывает бизнес-доступ к площадке (есть аккредитация, ожидает и т.д.), `adapter_status` —
    реализован ли для неё сбор в этой системе и почему нет, если не реализован (раздел 9,
    этапы 2 и 12 ТЗ — источники подключаются волнами)."""

    NOT_IMPLEMENTED = "not_implemented"
    IMPLEMENTED = "implemented"
    BLOCKED = "blocked"


class PollingSchedule(str, enum.Enum):
    TWICE_DAILY = "twice_daily"
    DAILY = "daily"
    HOURLY = "hourly"
    # Каталог собственной продукции меняется редко — еженедельного обхода сайта
    # производителя достаточно, а чаще значило бы напрасно ходить по чужому серверу.
    WEEKLY = "weekly"


# Типы источников, которые пополняют справочник продукции, а не собирают тендеры.
# Опрос тендеров обязан их пропускать: у них нет ни списка закупок, ни `list_new_tenders`,
# и попытка опросить их как площадку дала бы в журнале ежедневную ложную ошибку.
CATALOG_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        SourceType.FGIS.value,
        SourceType.MANUFACTURER_SITE.value,
        SourceType.UPPER_SOFTWARE.value,
        SourceType.ADMISSION_REGISTRY.value,
    }
)

# Ключ системного источника ручных заявок (см. `SourceType.MANUAL`). Создаётся миграцией
# 0041 и из интерфейса не удаляется — на него ссылаются все заявки.
MANUAL_SOURCE_KEY = "manual"

# Что опрос тендеров и проверка доступности обязаны пропускать: справочные источники
# (тендеров не отдают) и ручные заявки (сайта нет вовсе — пинговать нечего, а «адаптер не
# реализован» в журнале по нему было бы ежедневной ложной ошибкой).
POLL_EXCLUDED_SOURCE_TYPES: frozenset[str] = CATALOG_SOURCE_TYPES | {SourceType.MANUAL.value}


class AvailabilityStatus(str, enum.Enum):
    """Результат последней автоматической проверки доступности площадки (не запрос данных,
    просто "отвечает ли сайт") — раз в минуту, см. `app/services/availability_service.py` и
    `app/core/scheduler.py`. Независима от `adapter_status`: площадка может быть доступна, но
    адаптер для неё ещё не реализован, или наоборот, временно недоступна (тех. работы)."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class Source(Base):
    """Источник тендеров (раздел 4, 7 ТЗ) — ЕИС + коммерческие ЭТП. Из двенадцати площадок
    ТЗ в системе осталось девять: три неподключаемые (РТС-тендер, АСТ ГОЗ, ЭТП ГПБ «Стратег»)
    удалены по итогам показа 15.09.2026, см. миграцию 0038 и `app/seed/sources_data.py`.
    Корпоративные площадки застройщиков (раздел 4.4 ТЗ) сюда же заносятся со статусом
    `pending_access`, без реализации адаптера."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SourceStatus.ACTIVE.value
    )
    polling_schedule: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PollingSchedule.TWICE_DAILY.value
    )
    adapter_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    adapter_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AdapterStatus.NOT_IMPLEMENTED.value
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    availability_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    availability_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    availability_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
