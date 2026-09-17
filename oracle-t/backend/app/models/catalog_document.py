"""Справочник документов по СИ и руководств по эксплуатации с датой их актуальности
(правка по итогам показа 15.09.2026, п.2).

Что здесь решается. Обход сайтов производителей и ревалидация ФГИС оставляют в справочнике
продукции **ссылки** на документы — руководство, паспорт, «Описание типа», сертификат,
декларацию, — но не отвечают на вопрос, которым задаётся человек перед подачей заявки:
«а документ по этой ссылке всё ещё тот, что мы читали, или производитель его переиздал?».
Руководство переиздаётся вместе с прошивкой, «Описание типа» — приказом Росстандарта, и
характеристики в справочнике, снятые со старой редакции, тихо устаревают.

Поэтому у каждого документа хранится **актуальная дата** — та, что сообщает сам источник
(заголовок `Last-Modified` у файла на сайте производителя, номер редакции у «Описания типа»
в ФГИС), а если источник её не сообщает — дата, когда система сама зафиксировала изменение
содержимого. Раз в неделю все документы сверяются с источником заново, и обо всех
изменениях уходит отдельный отчёт на почту (`app/services/document_registry_service.py`).

Почему отдельная таблица, а не поля в `product_characteristics`. Ссылки там и остаются —
это значения характеристик группы «Документация» Приложения C, и трогать их незачем. Но
у документа своя жизнь: у него есть отпечаток (ETag, размер, хеш), история изменений и
статус проверки, и ни одно из этих полей не является характеристикой прибора. Кроме того,
один документ относится и к моделям (руководство), и к кодам СИ («Описание типа») — у
характеристик такого второго родителя нет."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CatalogDocumentKind(str, enum.Enum):
    """Назначение документа. Определяется по полю «Документация» Приложения C, откуда
    взята ссылка, а для ФГИС — по самому источнику."""

    MANUAL = "manual"  # руководство по эксплуатации
    PASSPORT = "passport"  # паспорт прибора
    DESCRIPTION_TYPE = "description_type"  # «Описание типа» средства измерений
    CERTIFICATE = "certificate"  # свидетельство (сертификат) об утверждении типа
    DECLARATION = "declaration"  # декларация о соответствии


KIND_TITLES: dict[str, str] = {
    CatalogDocumentKind.MANUAL.value: "Руководство по эксплуатации",
    CatalogDocumentKind.PASSPORT.value: "Паспорт прибора",
    CatalogDocumentKind.DESCRIPTION_TYPE.value: "Описание типа СИ",
    CatalogDocumentKind.CERTIFICATE.value: "Сертификат об утверждении типа",
    CatalogDocumentKind.DECLARATION.value: "Декларация о соответствии",
}

# Поле группы «Документация» Приложения C → назначение документа. Именно эти поля
# заполняет обход сайта производителя (`catalog_site_sync._document_values`).
FIELD_TO_KIND: dict[str, CatalogDocumentKind] = {
    "Ссылка на руководство": CatalogDocumentKind.MANUAL,
    "Ссылка на паспорт": CatalogDocumentKind.PASSPORT,
    "Ссылка на описание типа": CatalogDocumentKind.DESCRIPTION_TYPE,
    "Ссылка на сертификат": CatalogDocumentKind.CERTIFICATE,
    "Ссылка на декларацию": CatalogDocumentKind.DECLARATION,
}


class CatalogDocumentSource(str, enum.Enum):
    MANUFACTURER_SITE = "manufacturer_site"
    FGIS = "fgis"


class DocumentDateSource(str, enum.Enum):
    """Откуда взята актуальная дата документа — человеку важно знать, насколько ей верить.

    `last_modified` — сервер сообщил дату файла; `fgis_version` — номер редакции «Описания
    типа» в реестре (сама дата — момент, когда система эту редакцию увидела); `observed` —
    источник даты не сообщает, и это дата, когда система впервые увидела документ либо
    зафиксировала изменение его содержимого."""

    LAST_MODIFIED = "last_modified"
    FGIS_VERSION = "fgis_version"
    OBSERVED = "observed"


class DocumentCheckStatus(str, enum.Enum):
    """Итог последней сверки с источником."""

    OK = "ok"
    UNAVAILABLE = "unavailable"  # источник не ответил либо вернул ошибку
    FORBIDDEN_BY_ROBOTS = "forbidden_by_robots"  # robots.txt сайта закрывает файл
    NOT_CHECKED = "not_checked"  # запись заведена, сверка ещё не выполнялась


class CatalogDocument(Base):
    """Один документ по одной записи справочника — модели прибора либо коду СИ.

    Один и тот же файл может стоять у нескольких моделей (сертификат у МИРТЕК общий на
    семейство) — и это осознанно разные строки: человек смотрит на документы конкретной
    модели, а отпечаток файла у всех строк совпадёт сам собой (сверка при этом ходит по
    каждому адресу один раз за прогон)."""

    __tablename__ = "catalog_documents"
    __table_args__ = (
        # У модели (и у кода СИ) — не больше одного документа каждого назначения: ровно
        # столько ссылок оставляет обход сайта в полях «Документация» Приложения C. Замена
        # файла по другому адресу при этом — изменение того же документа, а не пара
        # «пропал + появился», поэтому адрес в ключ не входит.
        # Два частичных индекса вместо одного ограничения: у строки заполнен либо
        # `product_id`, либо `si_type_id`, а NULL в уникальном ограничении не равен NULL
        # (`NULLS NOT DISTINCT` требует PostgreSQL 15, установщик на Debian/Ubuntu ставит
        # версию дистрибутива).
        Index(
            "uq_catalog_documents_product_kind",
            "product_id",
            "kind",
            unique=True,
            postgresql_where=text("product_id IS NOT NULL"),
        ),
        Index(
            "uq_catalog_documents_si_type_kind",
            "si_type_id",
            "kind",
            unique=True,
            postgresql_where=text("si_type_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    si_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("si_types.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    # Актуальная дата документа и её происхождение — см. `DocumentDateSource`.
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_date_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Номер редакции у «Описания типа» ФГИС; у файлов с сайта пусто.
    version_label: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Отпечаток файла на момент последней сверки. Сравниваются в порядке надёжности:
    # ETag → Last-Modified → размер; хеш считается только когда сервер не отдаёт ничего
    # из этого (тогда файл приходится скачать целиком).
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified_header: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_length: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Ссылка всё ещё стоит в справочнике продукции. Когда обход сайта перестаёт её
    # находить, строка не удаляется, а гаснет: человеку нужно увидеть в отчёте, что документ
    # пропал с сайта, — это тоже событие актуальности.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    check_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=DocumentCheckStatus.NOT_CHECKED.value
    )
    check_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Когда система последний раз зафиксировала, что документ изменился, и сколько раз
    # это случалось. По `changed_at` строится отчёт и подсвечиваются свежие изменения.
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
