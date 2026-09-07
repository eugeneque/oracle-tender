"""Скачивание и разбор документов тендера (раздел 5.2 ТЗ). Запускается лениво, по требованию
(при первом открытии карточки тендера в интерфейсе — `GET /tenders/{id}/documents`), а не при
каждом плановом опросе источника: скачивать документацию по всем тысячам собранных тендеров
сразу было бы затратно и бессмысленно, если их никто не открывает."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger
from sqlalchemy import or_ as sa_or, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify
from app.adapters.registry import get_adapter
from app.core.config import get_settings
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_document import DocumentClass, ParseStatus, TenderDocument
from app.services.audit import log_action
from app.services.document_extraction import EXTRACTION_VERSION, extract_text, sniff_extension

DOWNLOAD_TIMEOUT_SECONDS = 60.0
# Пауза между файлами одного комплекта. Площадки (заметнее всего файловый хост ЭТП ГПБ)
# рвут соединение, когда документы запрашиваются подряд без задержки: повторные попытки это
# вытягивают, но половина комплекта скачивалась только со второго-третьего раза, а часть
# терялась совсем. Секунда между файлами дешевле, чем потерянная документация.
DELAY_BETWEEN_DOCUMENTS_SECONDS = 1.0
# Попыток на файл больше, чем на обычный запрос к источнику: разрывы соединения на файловых
# хостах площадок случайны (один и тот же файл со второй-пятой попытки скачивается целиком),
# а цена отказа выше — без документа тендер остаётся без анализа требований.
DOWNLOAD_RETRY_ATTEMPTS = 5
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # 50 МБ — защитный предел на один файл


class DocumentTooLargeError(ValueError):
    """Файл превышает предел размера — повторять скачивание бессмысленно."""


def get_storage_root() -> Path:
    root = Path(get_settings().storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _guess_extension(file_name: str, url: str) -> str | None:
    # `.strip()`: как минимум ЕИС отдаёт имена файлов с посторонним пробелом на конце
    # (наблюдалось на реальных данных) — без него расширение получалось вида ".pdf " и не
    # совпадало ни с одним расширением в app/services/document_extraction.py.
    for candidate in (file_name.strip(), url.split("?", 1)[0]):
        suffix = Path(candidate).suffix.strip().lower()
        if suffix and len(suffix) <= 6:
            return suffix
    return None


def _download_bytes(url: str) -> bytes:
    """Скачивает файл с повторными попытками (раздел 5.9 ТЗ: «при ошибке загрузки — повторные
    попытки через настраиваемый интервал»).

    Повтор здесь не теоретический: файловый хост ЭТП ГПБ штатно рвёт соединение на втором
    файле подряд (`Server disconnected without sending a response`) — при последовательном
    скачивании комплекта документации без повтора терялась бы половина файлов.
    """

    settings = get_settings()
    attempts = max(settings.http_retry_attempts, DOWNLOAD_RETRY_ATTEMPTS)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                verify=resolve_verify(url),
                follow_redirects=True,
            ) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    chunks = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_DOCUMENT_BYTES:
                            # Превышение предела — не сетевой сбой: повторять бессмысленно.
                            raise DocumentTooLargeError(
                                f"Файл больше предела {MAX_DOCUMENT_BYTES // (1024 * 1024)} МБ"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except DocumentTooLargeError:
            raise
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < attempts:
                delay = min(settings.http_retry_backoff_base * (2 ** (attempt - 1)), 15.0)
                logger.warning(
                    f"Скачивание {url} не удалось (попытка {attempt} из {attempts}): {exc}; "
                    f"повтор через {delay:.1f} с"
                )
                time.sleep(delay)

    raise last_error if last_error else RuntimeError("Файл не скачан")


def _process_document(doc: TenderDocument, tender_dir: Path) -> None:
    try:
        content = _download_bytes(doc.source_url)
    except Exception as exc:  # noqa: BLE001 - ошибка одного документа не должна прервать остальные
        doc.parse_status = ParseStatus.ERROR.value
        doc.parse_error = f"Не удалось скачать: {exc}"
        return

    # Тип определяем по имени/ссылке, а если там ничего не сказано — по содержимому файла:
    # часть площадок отдаёт документ по ссылке вида `/file/get/id/12345`, без расширения,
    # и такой документ раньше сохранялся безымянным и оставался без текста.
    doc.file_type = (
        doc.file_type
        or _guess_extension(doc.file_name, doc.source_url)
        or sniff_extension(content)
    )
    file_path = tender_dir / f"{doc.id}{doc.file_type or ''}"
    file_path.write_bytes(content)
    doc.storage_path = str(file_path.relative_to(get_storage_root()))
    doc.downloaded_at = datetime.now(timezone.utc)

    try:
        doc.extracted_text = extract_text(doc.file_type, content)
        doc.parse_status = ParseStatus.SUCCESS.value
        doc.extraction_version = EXTRACTION_VERSION
    except Exception as exc:  # noqa: BLE001 - файл уже скачан и сохранён, ошибка — только в разборе текста
        doc.parse_status = ParseStatus.ERROR.value
        doc.parse_error = f"Файл скачан, но текст не извлечён: {exc}"


def reextract_stale_documents(db: Session, tender: Tender) -> int:
    """Переразбирает документы, разобранные устаревшими правилами (см. `EXTRACTION_VERSION`).

    Файл берётся из хранилища, заново по сети не скачивается: правила поменялись, а сам файл
    тот же. Без этого изменение разбора действовало бы только на будущие тендеры, а у всех
    ранее собранных текст навсегда остался бы неполным — например, без технического задания,
    лежащего внутри вложенного архива.

    Возвращает число переразобранных документов.
    """

    documents = list(
        db.scalars(
            select(TenderDocument).where(
                TenderDocument.tender_id == tender.id,
                TenderDocument.storage_path.is_not(None),
                sa_or(
                    TenderDocument.extraction_version.is_(None),
                    TenderDocument.extraction_version < EXTRACTION_VERSION,
                ),
            )
        )
    )

    updated = 0
    for doc in documents:
        file_path = get_storage_root() / (doc.storage_path or "")
        if not file_path.is_file():
            continue  # файл удалён из хранилища — оставляем текст, который уже есть
        try:
            text = extract_text(doc.file_type, file_path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - один документ не должен ронять анализ тендера
            logger.warning(f"Переразбор документа «{doc.file_name}» не удался: {exc}")
            continue

        doc.extraction_version = EXTRACTION_VERSION
        if text:
            doc.extracted_text = text
            doc.parse_status = ParseStatus.SUCCESS.value
            doc.parse_error = None
        updated += 1

    if updated:
        db.commit()
    return updated


# Автоклассификация документа по имени файла (раздел 5.6 ТЗ, вкладка «Документы»).
# Детерминированно, а не моделью: имя файла на площадках говорящее («ТЗ.docx»,
# «Проект контракта.pdf», «Смета.xlsx»), и тратить на это обращение к модели по каждому
# файлу — платить за то, что решается перечнем подстрок. Что не опознано — `other`, и
# человек поправит класс вручную.
_CLASS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        DocumentClass.TZ_DESCRIPTION.value,
        ("тех", "тз", "техническое задание", "описание объекта", "оод", "спецификац"),
    ),
    (DocumentClass.SSR.value, ("смет", "сср", "расчет цены", "расчёт цены", "нмцк", "кс-2")),
    (DocumentClass.CONTRACT.value, ("контракт", "договор", "соглашен")),
    (DocumentClass.PROTOCOL.value, ("протокол",)),
    (DocumentClass.NOTICE.value, ("извещ", "объявлен")),
)


def classify_document(file_name: str) -> str:
    """Класс документа по имени файла. Порядок перечня — от более специфичного к общему:
    «протокол рассмотрения заявок по проекту контракта» должен стать протоколом, а не
    контрактом."""

    name = (file_name or "").lower()
    for document_class, markers in _CLASS_MARKERS:
        if any(marker in name for marker in markers):
            return document_class
    return DocumentClass.OTHER.value


def list_tender_documents(db: Session, tender_id: uuid.UUID) -> list[TenderDocument]:
    return list(
        db.scalars(
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender_id)
            .order_by(TenderDocument.created_at)
        )
    )


def sync_tender_documents(db: Session, tender: Tender) -> list[TenderDocument]:
    """Скачивает документы тендера при первом обращении; при повторном — просто возвращает уже
    сохранённые записи (не перекачивает заново). Источник без реализованного адаптера — не
    ошибка, просто пустой список (у площадки могло не быть перечня документов вовсе)."""

    existing = list_tender_documents(db, tender.id)
    if existing:
        return existing

    adapter = get_adapter(tender.source.adapter_key)
    if adapter is None:
        return []

    try:
        doc_refs = adapter.download_documents(tender.external_id, tender.source_url)
    except Exception as exc:  # noqa: BLE001 - изоляция сбоя одного тендера от остальных (раздел 5.1, 5.9 ТЗ)
        logger.warning(f"Не удалось получить список документов тендера {tender.id}: {exc}")
        log_action(
            db,
            component="documents",
            action=f"sync_documents:{tender.external_id}",
            result="error",
            level=LogLevel.WARNING,
            details=str(exc),
        )
        db.commit()
        return []

    tender_dir = get_storage_root() / str(tender.id)
    tender_dir.mkdir(parents=True, exist_ok=True)

    results: list[TenderDocument] = []
    errors = 0
    seen_urls: set[str] = set()
    for ref in doc_refs:
        if ref.url in seen_urls:
            continue  # адаптер отдал дубль ссылки в одном вызове — не пытаемся вставить дважды
        seen_urls.add(ref.url)

        doc = TenderDocument(
            tender_id=tender.id,
            file_name=ref.file_name,
            file_type=_guess_extension(ref.file_name, ref.url),
            source_url=ref.url,
            document_class=classify_document(ref.file_name),
        )
        try:
            # Savepoint, а не обычный flush: конкурентный запрос за той же карточкой тендера
            # (двойной вызов эффекта в React StrictMode при разработке, два пользователя открыли
            # одну карточку одновременно и т.п.) мог успеть создать документ с этим же URL первым
            # — без savepoint конфликт уникального ограничения испортил бы всю транзакцию целиком,
            # а не только эту одну вставку.
            with db.begin_nested():
                db.add(doc)
                db.flush()
        except IntegrityError:
            existing_doc = db.scalar(
                select(TenderDocument).where(
                    TenderDocument.tender_id == tender.id, TenderDocument.source_url == ref.url
                )
            )
            if existing_doc:
                results.append(existing_doc)
            continue

        if results:
            time.sleep(DELAY_BETWEEN_DOCUMENTS_SECONDS)

        _process_document(doc, tender_dir)
        if doc.parse_status == ParseStatus.ERROR.value:
            errors += 1
        results.append(doc)

    log_action(
        db,
        component="documents",
        action=f"sync_documents:{tender.external_id}",
        result="success" if not errors else "partial_error",
        level=LogLevel.INFO if not errors else LogLevel.WARNING,
        details=f"Скачано {len(results)}, ошибок {errors}",
    )
    db.commit()
    for doc in results:
        db.refresh(doc)
    return results
