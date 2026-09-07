import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentClass(str, enum.Enum):
    """Автоклассификация типа документа (раздел 5.6 ТЗ, вкладка «Документы»). Нужна, чтобы
    расчёт брал техническое задание и смету, а не тонул в извещениях и протоколах."""

    TZ_DESCRIPTION = "tz_description"
    SSR = "ssr"
    CONTRACT = "contract"
    NOTICE = "notice"
    PROTOCOL = "protocol"
    OTHER = "other"


DOCUMENT_CLASS_LABELS: dict[str, str] = {
    DocumentClass.TZ_DESCRIPTION.value: "ТЗ / Описание",
    DocumentClass.SSR.value: "Смета (ССР)",
    DocumentClass.CONTRACT.value: "Проект контракта",
    DocumentClass.NOTICE.value: "Извещение",
    DocumentClass.PROTOCOL.value: "Протокол",
    DocumentClass.OTHER.value: "Прочее",
}


class ParseStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"


class TenderDocument(Base):
    """Документ тендера (раздел 5.2, 7 ТЗ). Скачивается лениво — при первом открытии карточки
    тендера в интерфейсе (`app/services/document_service.py`), а не при каждом плановом опросе
    источника: большинство собранных тендеров пользователь никогда не откроет, скачивать
    документацию по всем сразу было бы затратно и бессмысленно."""

    __tablename__ = "tender_documents"
    __table_args__ = (
        UniqueConstraint("tender_id", "source_url", name="uq_tender_documents_tender_source_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(600), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ParseStatus.PENDING.value
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Какими правилами извлечён текст (`document_extraction.EXTRACTION_VERSION`). Документ,
    # разобранный устаревшими правилами, переразбирается из сохранённого файла перед анализом
    # — заново скачивать его для этого не нужно.
    extraction_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_class: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Отметка человека «считать в первую очередь по этому файлу» (звёздочка в интерфейсе).
    # Автоклассификация ошибается, а какой файл главный — знает специалист.
    is_priority_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def has_text(self) -> bool:
        """Есть ли у документа извлечённый текст. Отдельный признак нужен интерфейсу:
        статус `success` сам по себе означает только «скачался и разобрался без ошибки», а у
        неподдержанного формата текст при этом пуст — по карточке это было неразличимо."""

        return bool(self.extracted_text and self.extracted_text.strip())
