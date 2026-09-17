"""Ручные заявки: закупка, которую заказчик прислал напрямую (решение 15.09.2026).

Площадки покрывают не всё: часть заказчиков присылает проект договора или ТЗ с
характеристиками приборов письмом, минуя торги. До этого такую закупку было некуда
положить — ни карточки, ни анализа требований, ни матрицы соответствия. Теперь её заводят
руками: наименование, заказчик, файлы, — и дальше она проходит тот же конвейер, что и
собранный тендер (раздел 5.4-5.6 ТЗ): извлечение требований, расчёт процента победителя,
история и комментарии.

Отличий от собранного тендера два, и оба на входе:

* источник — системная строка `MANUAL_SOURCE_KEY` («Заявка»), а не площадка;
* документы не скачиваются по ссылке, а приходят файлами; в `source_url` у них
  служебный адрес `upload://…` — поле обязательное и уникальное в пределах тендера, а
  скачать такой документ «заново» неоткуда, только из хранилища.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.source import MANUAL_SOURCE_KEY, Source
from app.models.tender import Tender, TenderStage, TenderStatus
from app.models.tender_document import ParseStatus, TenderDocument
from app.models.tender_history import HistoryKind, TenderHistoryEntry
from app.models.user import User
from app.services.audit import log_action
from app.services.document_extraction import EXTRACTION_VERSION, extract_text, sniff_extension
from app.services.document_service import (
    MAX_DOCUMENT_BYTES,
    _guess_extension,
    classify_document,
    get_storage_root,
)

# Префикс служебного адреса загруженного файла. По нему карточка отличает загруженный
# документ от скачанного с площадки (кнопка «скачать» у него ведёт в хранилище, а не на сайт).
UPLOAD_URL_PREFIX = "upload://"

# Номер заявки: «ЗАЯВКА-20260915-3F7A». Дата — чтобы номер читался человеком в списке и в
# журнале; хвост — чтобы две заявки одного дня не столкнулись на уникальном ограничении
# `(source_id, external_id)`.
_NUMBER_PREFIX = "ЗАЯВКА"


class ManualRequestError(ValueError):
    """Ошибка входных данных заявки — интерфейс показывает её текст как есть."""


@dataclass(frozen=True)
class UploadedFile:
    file_name: str
    content: bytes


def get_manual_source(db: Session) -> Source:
    source = db.scalar(select(Source).where(Source.key == MANUAL_SOURCE_KEY))
    if source is None:
        # Строка создаётся миграцией 0041; её отсутствие — не пользовательская ошибка, а
        # неприменённая миграция, и прятать это за 422 было бы неверно.
        raise RuntimeError(
            f"Системный источник «{MANUAL_SOURCE_KEY}» не найден — примените миграции"
        )
    return source


def is_manual(tender: Tender) -> bool:
    return tender.source is not None and tender.source.key == MANUAL_SOURCE_KEY


def _new_number() -> str:
    return f"{_NUMBER_PREFIX}-{date.today():%Y%m%d}-{secrets.token_hex(2).upper()}"


def _deadline_at(deadline: date | None) -> datetime | None:
    """Срок подачи из даты: конец суток, чтобы фильтр «скрыть истёкшие» не прятал заявку в
    последний день, когда по ней ещё можно работать.

    Без часового пояса — так же, как сроки из адаптеров площадок (`app/adapters/eis.py`):
    наивное время база трактует в поясе сервера, и в карточке заявка показывает ту дату,
    которую человек выбрал, а не сдвинутую на смещение от UTC."""

    if deadline is None:
        return None
    return datetime.combine(deadline, time(23, 59, 59))


def create_manual_request(
    db: Session,
    *,
    title: str,
    customer_name: str | None,
    price: Decimal | None,
    application_end: date | None,
    comment: str | None,
    files: list[UploadedFile],
    actor: User,
) -> tuple[Tender, list[TenderDocument]]:
    """Заводит заявку и прикладывает к ней файлы. Возвращает тендер и его документы.

    Заявка сразу попадает на этап «на проверке», а не «подобрано ИИ»: её принёс человек,
    и решение «эта закупка наша» уже принято — ИИ-отбор здесь заменён самим фактом
    создания. По той же причине `passed_relevance_filter=True`: профиль ключевых слов —
    сито для площадок, а не для того, что заказчик прислал сам. `ai_relevant` остаётся
    пустым — это решение модели, и подделывать его не следует.
    """

    title = (title or "").strip()
    if not title:
        raise ManualRequestError("Укажите наименование закупки")
    if price is not None and price < 0:
        raise ManualRequestError("Сумма не может быть отрицательной")

    source = get_manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=_new_number(),
        title=title,
        customer_name=(customer_name or "").strip() or None,
        price=price,
        currency="RUB",
        status=TenderStatus.COLLECTING_BIDS.value,
        publish_date=date.today(),
        application_start=datetime.now(timezone.utc),
        application_end=_deadline_at(application_end),
        stage=TenderStage.UNDER_REVIEW.value,
        passed_relevance_filter=True,
        assignee_id=actor.id,
    )
    db.add(tender)
    db.flush()

    note = (comment or "").strip()
    if note:
        db.add(
            TenderHistoryEntry(
                tender_id=tender.id,
                user_id=actor.id,
                kind=HistoryKind.COMMENT.value,
                comment=note,
            )
        )

    documents = attach_uploaded_documents(db, tender, files, actor=actor, commit=False)

    log_action(
        db,
        component="tenders",
        action=f"create_manual_request:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=f"«{title[:200]}»; файлов: {len(documents)}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(tender)
    for document in documents:
        db.refresh(document)
    return tender, documents


def attach_uploaded_documents(
    db: Session,
    tender: Tender,
    files: list[UploadedFile],
    *,
    actor: User,
    commit: bool = True,
) -> list[TenderDocument]:
    """Сохраняет загруженные файлы как документы тендера и сразу извлекает из них текст.

    Разбор здесь, а не в фоновой задаче: файл уже в памяти, а человек, приложивший договор,
    хочет увидеть «текст распознан» до того, как нажмёт «Анализ». Ошибка разбора одного файла
    не отменяет остальные — документ сохраняется со статусом ошибки, как и при скачивании.
    """

    if not files:
        return []

    tender_dir = get_storage_root() / str(tender.id)
    tender_dir.mkdir(parents=True, exist_ok=True)

    documents: list[TenderDocument] = []
    for upload in files:
        file_name = (upload.file_name or "").strip() or "файл"
        if not upload.content:
            raise ManualRequestError(f"Файл «{file_name}» пуст")
        if len(upload.content) > MAX_DOCUMENT_BYTES:
            raise ManualRequestError(
                f"Файл «{file_name}» больше предела {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ"
            )

        document = TenderDocument(
            id=uuid.uuid4(),
            tender_id=tender.id,
            file_name=file_name,
            document_class=classify_document(file_name),
        )
        document.source_url = f"{UPLOAD_URL_PREFIX}{document.id}/{file_name}"
        document.file_type = _guess_extension(file_name, "") or sniff_extension(upload.content)

        file_path = tender_dir / f"{document.id}{document.file_type or ''}"
        file_path.write_bytes(upload.content)
        document.storage_path = str(file_path.relative_to(get_storage_root()))
        document.downloaded_at = datetime.now(timezone.utc)

        try:
            document.extracted_text = extract_text(document.file_type, upload.content)
            document.parse_status = ParseStatus.SUCCESS.value
            document.extraction_version = EXTRACTION_VERSION
        except Exception as exc:  # noqa: BLE001 - файл сохранён, не удался только разбор текста
            document.parse_status = ParseStatus.ERROR.value
            document.parse_error = f"Файл сохранён, но текст не извлечён: {exc}"

        db.add(document)
        documents.append(document)

    db.flush()
    log_action(
        db,
        component="documents",
        action=f"upload_documents:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details="; ".join(
            f"{d.file_name} ({'текст извлечён' if d.has_text else 'без текста'})"
            for d in documents
        )[:1000],
        user_id=actor.id,
    )
    if commit:
        db.commit()
        for document in documents:
            db.refresh(document)
    return documents
