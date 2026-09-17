"""Справочник продукции: производители, коды СИ, модели приборов, характеристики (раздел 5.3,
7 ТЗ — Этап 4). Источники и приоритет заполнения — раздел 5.3 ТЗ: ФГИС → сайт производителя →
ручной ввод/импорт (последний имеет приоритет при конфликте)."""

import enum
import uuid
from datetime import date, datetime

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiTypeSource(str, enum.Enum):
    """Как заполнен код СИ (раздел 5.3 ТЗ, п.11 истории решений): автопоиск по умолчанию,
    с проверкой человеком; ручной ввод и импорт — когда заказчик предоставит точные коды
    конкурентов, имеют приоритет при конфликте."""

    AUTO_SEARCH = "auto_search"
    MANUAL = "manual"
    IMPORT = "import"


class ProductStatus(str, enum.Enum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    ARCHIVED = "archived"


class CharacteristicSource(str, enum.Enum):
    """Источник конкретного значения характеристики — раздел 5.3 ТЗ: приоритет
    `manual_entry` > `user_manual` > `manufacturer_site` > `fgis_description_type` при
    конфликте (ручная правка не должна перетираться повторным автозаполнением)."""

    FGIS_DESCRIPTION_TYPE = "fgis_description_type"
    MANUFACTURER_SITE = "manufacturer_site"
    USER_MANUAL = "user_manual"
    MANUAL_ENTRY = "manual_entry"
    # Ссылка на документ, найденная поиском в интернете (Яндекс) на официальном сайте
    # производителя — когда в каталоге сайта документа о приборе ещё нет, а через поиск он
    # находится (случай НАРТИС-И100-W115). Отдельный источник, а не `manufacturer_site`:
    # человек при проверке должен видеть, что ссылку подобрала поисковая выдача, а не обход
    # каталога.
    WEB_SEARCH = "web_search"


class ProductDataSource(str, enum.Enum):
    """Откуда пришли данные записи каталога в целом — отдельная ось от
    `CharacteristicSource`, который описывает происхождение ОДНОГО значения.

    Нужно модулю сопоставления, чтобы расставлять приоритет между источниками осознанно:
    для продукции МИРТЕК сайт производителя полнее ФГИС (в «Описании типа» — только
    сертификационные данные, на сайте — весь набор эксплуатационных характеристик), а для
    конкурентов, наоборот, ФГИС — единственный надёжный источник."""

    FGIS = "fgis"
    MANUFACTURER_SITE = "manufacturer_site"
    MANUAL = "manual"


class ReviewStatus(str, enum.Enum):
    """Требует ли запись внимания человека и почему.

    `NEEDS_REVIEW` ставится в двух случаях, и оба — там, где автоматика обязана
    остановиться, а не гадать:

    * реестр ФГИС вернул несколько кандидатов либо кандидата не того вида измерений
      (коллизия наименований — случай «Пульсар», см. `app/adapters/fgis_matching.py`);
    * модель пропала со страницы категории сайта производителя при повторном обходе —
      удалять её автоматически нельзя, решение за человеком (п.2.4 задания).

    Это НЕ то же самое, что `verified_by_user`: там «человек подтвердил корректность»,
    здесь — «системе не хватило оснований решить самой»."""

    OK = "ok"
    NEEDS_REVIEW = "needs_review"


class Manufacturer(Base):
    """МИРТЕК (заказчик) + 12 конкурентов (раздел 4.3 ТЗ). Список расширяемый — новый
    производитель добавляется строкой, без переработки схемы (раздел 6.3 ТЗ)."""

    __tablename__ = "manufacturers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    legal_name: Mapped[str] = mapped_column(String(500), nullable=False)
    brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_mirtek: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SiType(Base):
    """Утверждённый тип СИ (раздел 2, 5.3, 7 ТЗ) — запись Госреестра средств измерений;
    одному типу соответствует множество моделей/исполнений (см. `Product.si_type_id`).

    `si_code` — **номер в Госреестре** (`61891-15`). Ключом выбран именно он, а не
    «Обозначение типа»: обозначение приходит из реестра в разных кавычках («МИРТЕК-212-РУ»,
    "МИРТЕК-312-РУ", МИРТЕК-101), хранится в поле-списке и не уникально, тогда как номер ГРСИ
    стабилен. Само обозначение лежит рядом, в `notation`.

    Важно для сопоставления с тендером: у одного типа бывает несколько версий «Описания
    типа» (у 61891-15 — четыре, по разным приказам Росстандарта), поэтому хранится версия
    загруженного документа — иначе система будет молча сверять требования тендера
    с отменённой редакцией."""

    __tablename__ = "si_types"
    __table_args__ = (
        UniqueConstraint("manufacturer_id", "si_code", name="uq_si_types_manufacturer_si_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id"), nullable=False
    )
    si_code: Mapped[str] = mapped_column(String(100), nullable=False)
    notation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    type_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # UUID карточки типа в ФГИС — по нему запрашивается карточка при повторном обновлении,
    # чтобы не искать запись по реестру заново.
    mit_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description_type_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ссылка на тот же документ в зеркале Госреестра: штатный файловый эндпоинт ФГИС
    # регулярно недоступен (см. докстринг app/adapters/fgis.py).
    description_type_mirror_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_type_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description_type_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Редакция «Описания типа», из которой характеристики уже разнесены по привязанным
    # моделям. Не совпадает с `description_type_version` — вышла новая редакция, разбор
    # надо повторить: характеристики в справочнике описывают отменённую редакцию.
    description_type_extracted_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    # Когда ревалидация заметила новую редакцию документа. Человеку в интерфейсе нужна
    # именно дата: «описание типа изменилось» без неё не отличить от давно известного.
    description_type_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    allowed_modifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Исполнения, представленные на испытания, из карточки типа — список полных условных
    # обозначений («НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D»). Реестр
    # узнаёт о новом исполнении раньше сайта производителя: у НАРТИС-И100 корпус W115
    # появился в редакции 2 «Описания типа», а в каталоге на сайте его нет до сих пор.
    tested_modifications: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    mpi_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_actual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Как запись сопоставлена с производителем при автопоиске: `legal` — совпало юридическое
    # название, `brand` — только торговое имя (требует особого внимания при проверке, см.
    # `SiSearchResult.matched_by`).
    matched_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SiTypeSource.AUTO_SEARCH.value
    )
    verified_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # «Требует ручной проверки» — результат дисамбигуации кандидатов реестра
    # (см. `ReviewStatus` и `app/adapters/fgis_matching.py`). `review_reason` хранит
    # человекочитаемое объяснение со списком кандидатов: без него пометка бесполезна —
    # человек всё равно пойдёт искать в ФГИС руками.
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReviewStatus.OK.value
    )
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Когда карточку типа последний раз сверяли с реестром (ревалидация по расписанию,
    # п.1.1 задания). Отличается от `updated_at`: тот меняется при любой правке записи,
    # а очередь ревалидации должна брать самые давно не проверявшиеся.
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Product(Base):
    """Конкретная модель прибора (раздел 7 ТЗ). `si_type_id` может быть `NULL` до момента,
    пока код СИ не найден/не привязан вручную — характеристики при этом всё равно можно
    заполнять с сайта производителя (раздел 5.3 ТЗ, источник 2)."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id"), nullable=False
    )
    si_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("si_types.id"), nullable=True
    )
    # Наименование как у производителя: «Счётчик электрической энергии однофазный
    # интеллектуальный НАРТИС-Р1-М». Именно в таком виде прибор называют в закупочной
    # документации, и в таком же виде его должен видеть человек, открывший справочник.
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Обозначение модели, вычлененное из наименования («НАРТИС-Р1-М»). Нужно для другого —
    # по нему модель сопоставляется с обозначением типа в Госреестре: внутри полного
    # наименования обозначение префиксным сравнением не находится. `NULL` у записей ручного
    # ввода и импорта, где кода нет и ключом остаётся само наименование.
    model_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    article: Mapped[str | None] = mapped_column(String(100), nullable=True)
    device_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Заводское исполнение модели («Таганрог», «Владивосток»). У МИРТЕК это не косметика:
    # у одной и той же модели исполнения различаются сроком службы, наработкой на отказ и
    # комплектом документов — проверено на МИРТЕК-12-РУ-D17 (Таганрог 48 лет / Владивосток
    # 35 лет). Поэтому исполнение — отдельная запись каталога, а не атрибут одной.
    execution: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Полное условное обозначение исполнения из реестра ФГИС — у записей, заведённых из
    # Аршина, а не с сайта. В нём закодированы характеристики (корпус, ток, интерфейсы), и
    # вместе с легендой структуры обозначения из карточки типа оно расшифровывается моделью.
    registry_modification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProductStatus.ACTIVE.value
    )
    # URL карточки на сайте производителя — уникальный идентификатор записи со стороны
    # источника. Именно он, а не тройка «производитель + модель + артикул», делает повторный
    # обход идемпотентным: артикул на сайте может смениться, адрес карточки — нет.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    data_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Характеристики с сайта, которым не нашлось поля в Приложении C. Не выбрасываются:
    # набор полей на сайте производителя шире справочника и меняется без предупреждения,
    # а потеря значения обнаружится только когда по нему придёт требование тендера.
    extra_specifications: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReviewStatus.OK.value
    )
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Когда запись последний раз встретилась на странице категории. По нему находятся
    # позиции, пропавшие с сайта: они помечаются «требует проверки», а не удаляются
    # (п.2.4 задания — окончательное решение за пользователем).
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductCharacteristic(Base):
    """EAV-хранилище характеристик (раздел 5.3, 7 ТЗ — ~80 полей по 11 группам, Приложение C
    ТЗ). EAV, а не широкая таблица с 80 колонками: набор характеристик по группам различается
    между типами приборов (эл.счётчик/водо-/тепло-), и заполняется постепенно из разных
    источников — фиксированная схема потребовала бы миграции при каждом новом поле."""

    __tablename__ = "product_characteristics"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "group_name", "field_name", name="uq_product_characteristics_field"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    group_name: Mapped[str] = mapped_column(String(100), nullable=False)
    field_name: Mapped[str] = mapped_column(String(150), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    verified_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
