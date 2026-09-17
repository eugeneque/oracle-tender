"""Справочник актуальности документов по СИ и руководств по эксплуатации (правка по итогам
показа 15.09.2026, п.2): наполнение, еженедельная сверка с источниками и отчёт.

Три шага, и все три — в одном прогоне (`run_check`):

1. **Синхронизация справочника** (`sync_registry`). Ссылки на документы уже лежат в
   справочнике продукции — обход сайта производителя кладёт их в поля группы «Документация»
   Приложения C, ревалидация ФГИС — в `si_types.description_type_url`. Отсюда они
   переносятся в `catalog_documents` по строке на документ; ссылка, пропавшая из
   справочника, гасит строку (`is_active=False`), а не удаляет: «документ пропал с сайта» —
   тоже событие актуальности, и человек должен его увидеть в отчёте.

2. **Сверка с источником** (`check_documents`). У файлов на сайтах производителей
   запрашиваются только заголовки (`HEAD`): ETag, Last-Modified, размер. Скачивать
   четыреста PDF по 5–20 МБ каждую неделю — незачем, когда сервер сам говорит, менялся ли
   файл; полностью файл читается (и хешируется) лишь если сервер не отдаёт ни одного из
   этих признаков. `robots.txt` сайта соблюдается, включая `Crawl-delay`. У «Описания типа»
   ФГИС сверка идёт без сети — по номеру редакции, который уже обновляет ревалидация ФГИС
   (`fgis_catalog_sync`); ходить в нестабильный сервис реестра второй раз незачем.

3. **Отчёт** (`notify_documents_updated`). Если что-то изменилось, появилось, пропало или
   стало недоступно — на почту уходит отдельное письмо со списком по производителям, и оно
   же остаётся в журнале уведомлений как отчёт системы. Тихая неделя письма не рождает:
   письмо «ничего не изменилось» раз в неделю быстро перестают читать.

Актуальная дата документа. Это дата, которую сообщает источник: `Last-Modified` у файла,
редакция у «Описания типа». Когда источник её не сообщает, датой становится день, когда
система впервые увидела документ либо зафиксировала изменение его содержимого, — и
происхождение даты хранится рядом (`document_date_source`), потому что этим двум датам
человек доверяет по-разному.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify
from app.core.timezones import now_msk
from app.models.catalog_document import (
    FIELD_TO_KIND,
    KIND_TITLES,
    CatalogDocument,
    CatalogDocumentKind,
    CatalogDocumentSource,
    DocumentCheckStatus,
    DocumentDateSource,
)
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, SiType
from app.models.notification import Notification
from app.services import catalog_queue_service, notification_service
from app.services.audit import log_action
from app.services.catalog_queue_service import TaskOutcome
from app.services.product_manual_ingest import RobotsGate

COMPONENT = "product_catalog"

# Ключ исполнителя в очереди справочника — для ручного запуска сверки из каталога. Не
# адаптер источника (у сверки нет своего сайта), но очередь та же: сверка идёт минуты, и
# держать на ней HTTP-запрос из браузера нельзя.
ADAPTER_KEY = "document_registry"

DOCUMENTATION_GROUP = "Документация"

REQUEST_TIMEOUT_SECONDS = 30.0
# Пауза между запросами к одному сайту, когда robots.txt своей не требует. Сверка идёт по
# чужим серверам сотнями запросов подряд, и без паузы это выглядит как атака.
DEFAULT_HOST_DELAY_SECONDS = 1.0
# Предел на файл, который приходится скачать целиком ради хеша (сервер не отдал ни ETag,
# ни Last-Modified, ни размера). Тот же, что у загрузки руководств.
MAX_HASH_BYTES = 40 * 1024 * 1024
# Сколько документов перечислять в одном разделе письма. Первый прогон видит «новыми» все
# четыреста документов сразу — письмо с таким списком никто не прочтёт.
REPORT_SECTION_LIMIT = 40


# --- Результаты ------------------------------------------------------------------------


@dataclass
class DocumentEvent:
    """Одно событие сверки для отчёта. `detail` — что именно изменилось, человекочитаемо."""

    document: CatalogDocument
    detail: str


@dataclass
class CheckOutcome:
    registry_size: int = 0
    checked: int = 0
    new: list[DocumentEvent] = field(default_factory=list)
    changed: list[DocumentEvent] = field(default_factory=list)
    removed: list[DocumentEvent] = field(default_factory=list)
    unavailable: list[DocumentEvent] = field(default_factory=list)
    skipped_by_robots: int = 0
    notification: Notification | None = None

    @property
    def has_updates(self) -> bool:
        return bool(self.new or self.changed or self.removed or self.unavailable)

    def summary(self) -> str:
        return (
            f"документов в справочнике {self.registry_size}, проверено {self.checked}, "
            f"изменилось {len(self.changed)}, появилось {len(self.new)}, "
            f"пропало {len(self.removed)}, недоступно {len(self.unavailable)}"
            + (f", закрыто robots.txt {self.skipped_by_robots}" if self.skipped_by_robots else "")
        )


@dataclass
class Fingerprint:
    """Что источник сообщил о файле. `None` во всех полях — файл недоступен (`error`)."""

    etag: str | None = None
    last_modified: str | None = None
    last_modified_date: date | None = None
    content_length: int | None = None
    content_hash: str | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.error is None

    def differs_from(self, document: CatalogDocument) -> str | None:
        """Чем отпечаток отличается от сохранённого; `None` — ничем.

        Признаки сравниваются в порядке надёжности, и первый же присутствующий с обеих
        сторон решает: ETag меняется вместе с содержимым, Last-Modified — тоже, а размер
        совпадает у разных файлов слишком часто, чтобы верить только ему. Признак, которого
        раньше не было, изменением не считается — иначе сервер, начавший отдавать ETag,
        «переиздал» бы все документы разом."""

        if self.etag and document.etag:
            if self.etag == document.etag:
                return None
            # Человеку про ETag говорить нечего — если сервер отдаёт и дату файла,
            # в отчёт идёт она.
            return self._date_change(document) or "файл заменён на сайте"
        if self.last_modified and document.last_modified_header:
            if self.last_modified == document.last_modified_header:
                return None
            return self._date_change(document) or "дата файла на сайте изменилась"
        if self.content_hash and document.content_hash:
            return None if self.content_hash == document.content_hash else "содержимое файла"
        if self.content_length is not None and document.content_length is not None:
            if self.content_length == document.content_length:
                return None
            return f"размер {document.content_length} → {self.content_length} байт"
        return None


    def _date_change(self, document: CatalogDocument) -> str | None:
        if self.last_modified_date and self.last_modified_date != document.document_date:
            return (
                f"дата файла {_format_date(document.document_date)} → "
                f"{_format_date(self.last_modified_date)}"
            )
        return None


# --- Шаг 1: синхронизация справочника ---------------------------------------------------


def sync_registry(
    db: Session, *, manufacturer: Manufacturer | None = None, outcome: CheckOutcome | None = None
) -> CheckOutcome:
    """Переносит ссылки из справочника продукции и кодов СИ в `catalog_documents`.

    Возвращает `outcome` с заполненными `new` (документы, которых в справочнике ещё не
    было) и `removed` (строки, чья ссылка из справочника пропала). Замена адреса у
    документа того же назначения — не «пропал + появился», а изменение: оно попадает в
    `changed` с прежним и новым адресом."""

    outcome = outcome or CheckOutcome()
    now = datetime.now(timezone.utc)
    seen: set[uuid.UUID] = set()

    # Документы моделей — из полей «Документация» Приложения C.
    characteristics = select(ProductCharacteristic, Product).join(
        Product, Product.id == ProductCharacteristic.product_id
    ).where(
        ProductCharacteristic.group_name == DOCUMENTATION_GROUP,
        ProductCharacteristic.field_name.in_(list(FIELD_TO_KIND)),
    )
    if manufacturer is not None:
        characteristics = characteristics.where(Product.manufacturer_id == manufacturer.id)
    for characteristic, product in db.execute(characteristics):
        url = (characteristic.value or "").strip()
        if not url.startswith("http"):
            continue
        kind = FIELD_TO_KIND[characteristic.field_name]
        document = _upsert(
            db,
            outcome,
            manufacturer_id=product.manufacturer_id,
            product_id=product.id,
            si_type_id=None,
            kind=kind,
            source=CatalogDocumentSource.MANUFACTURER_SITE,
            title=f"{KIND_TITLES[kind.value]} — {product.model_name}",
            url=url,
            now=now,
        )
        seen.add(document.id)

    # «Описание типа» кодов СИ — из реестра ФГИС. Версия документа известна без сети.
    si_types = select(SiType).where(SiType.description_type_url.is_not(None))
    if manufacturer is not None:
        si_types = si_types.where(SiType.manufacturer_id == manufacturer.id)
    for si_type in db.scalars(si_types):
        document = _upsert(
            db,
            outcome,
            manufacturer_id=si_type.manufacturer_id,
            product_id=None,
            si_type_id=si_type.id,
            kind=CatalogDocumentKind.DESCRIPTION_TYPE,
            source=CatalogDocumentSource.FGIS,
            title=f"Описание типа СИ {si_type.si_code}"
            + (f" ({si_type.notation})" if si_type.notation else ""),
            url=si_type.description_type_url or "",
            now=now,
            version_label=si_type.description_type_version,
            checked_at=si_type.last_checked_at,
        )
        seen.add(document.id)

    # Всё активное, чего в этом проходе не встретилось, — пропало из справочника.
    stale = select(CatalogDocument).where(CatalogDocument.is_active.is_(True))
    if manufacturer is not None:
        stale = stale.where(CatalogDocument.manufacturer_id == manufacturer.id)
    for document in db.scalars(stale):
        if document.id in seen:
            continue
        document.is_active = False
        document.changed_at = now
        outcome.removed.append(
            DocumentEvent(document, "ссылка пропала из справочника продукции")
        )

    db.flush()
    return outcome


def _upsert(
    db: Session,
    outcome: CheckOutcome,
    *,
    manufacturer_id: uuid.UUID,
    product_id: uuid.UUID | None,
    si_type_id: uuid.UUID | None,
    kind: CatalogDocumentKind,
    source: CatalogDocumentSource,
    title: str,
    url: str,
    now: datetime,
    version_label: str | None = None,
    checked_at: datetime | None = None,
) -> CatalogDocument:
    owner = (
        CatalogDocument.product_id == product_id
        if product_id is not None
        else CatalogDocument.si_type_id == si_type_id
    )
    document = db.scalar(
        select(CatalogDocument).where(owner, CatalogDocument.kind == kind.value)
    )
    if document is None:
        document = CatalogDocument(
            manufacturer_id=manufacturer_id,
            product_id=product_id,
            si_type_id=si_type_id,
            kind=kind.value,
            source=source.value,
            title=title[:500],
            url=url,
            version_label=version_label,
            first_seen_at=now,
        )
        if source is CatalogDocumentSource.FGIS:
            # Редакция известна из реестра — это и есть актуальность документа; сетевой
            # сверки у ФГИС нет, поэтому строка сразу считается проверенной.
            document.document_date = now_msk().date()
            document.document_date_source = DocumentDateSource.FGIS_VERSION.value
            document.check_status = DocumentCheckStatus.OK.value
            document.last_checked_at = checked_at or now
        db.add(document)
        db.flush()
        outcome.new.append(
            DocumentEvent(
                document,
                f"редакция {version_label}" if version_label else "новый документ в справочнике",
            )
        )
        return document

    document.title = title[:500]
    changes: list[str] = []
    if not document.is_active:
        # Ссылка вернулась в справочник — документ снова в строю; для отчёта это то же
        # событие, что и новый документ.
        document.is_active = True
        document.first_seen_at = now
        changes.append("документ снова в справочнике")
    if document.url != url:
        changes.append(f"адрес изменился: {document.url} → {url}")
        document.url = url
        # Прежний отпечаток относится к другому файлу — сравнивать с ним новый нечестно.
        document.etag = document.last_modified_header = document.content_hash = None
        document.content_length = None
    if source is CatalogDocumentSource.FGIS:
        document.last_checked_at = checked_at or now
        if version_label and version_label != document.version_label:
            changes.append(
                f"новая редакция «Описания типа» {document.version_label or '—'} → {version_label}"
            )
            document.version_label = version_label
            document.document_date = now_msk().date()
            document.document_date_source = DocumentDateSource.FGIS_VERSION.value
    if changes:
        document.changed_at = now
        document.change_count += 1
        outcome.changed.append(DocumentEvent(document, "; ".join(changes)))
    return document


# --- Шаг 2: сверка с источником ---------------------------------------------------------


def check_documents(
    db: Session,
    *,
    manufacturer: Manufacturer | None = None,
    outcome: CheckOutcome | None = None,
    gate: RobotsGate | None = None,
) -> CheckOutcome:
    """Сверяет активные документы с сайтов производителей с их источниками.

    Один адрес запрашивается один раз за прогон, сколько бы строк на него ни ссылалось:
    сертификат у МИРТЕК общий на всё семейство. Между запросами к одному сайту — пауза."""

    outcome = outcome or CheckOutcome()
    gate = gate or RobotsGate()
    now = datetime.now(timezone.utc)

    query = select(CatalogDocument).where(
        CatalogDocument.is_active.is_(True),
        CatalogDocument.source == CatalogDocumentSource.MANUFACTURER_SITE.value,
    )
    if manufacturer is not None:
        query = query.where(CatalogDocument.manufacturer_id == manufacturer.id)
    documents = list(db.scalars(query.order_by(CatalogDocument.url)))

    fingerprints: dict[str, Fingerprint] = {}
    last_request_at: dict[str, float] = {}
    for document in documents:
        if not gate.allows(document.url):
            document.check_status = DocumentCheckStatus.FORBIDDEN_BY_ROBOTS.value
            document.check_error = None
            document.last_checked_at = now
            outcome.skipped_by_robots += 1
            continue

        fingerprint = fingerprints.get(document.url)
        if fingerprint is None:
            _throttle(document.url, last_request_at, gate)
            fingerprint = _fetch_fingerprint(document.url)
            fingerprints[document.url] = fingerprint
        outcome.checked += 1
        _apply_fingerprint(document, fingerprint, now, outcome)

    db.flush()
    return outcome


def _throttle(url: str, last_request_at: dict[str, float], gate: RobotsGate) -> None:
    host = urlparse(url).netloc
    delay = gate.crawl_delay(url) or DEFAULT_HOST_DELAY_SECONDS
    previous = last_request_at.get(host)
    if previous is not None:
        wait = delay - (time.monotonic() - previous)
        if wait > 0:
            time.sleep(wait)
    last_request_at[host] = time.monotonic()


def _apply_fingerprint(
    document: CatalogDocument, fingerprint: Fingerprint, now: datetime, outcome: CheckOutcome
) -> None:
    document.last_checked_at = now

    if not fingerprint.available:
        # Недоступность попадает в отчёт один раз — когда обнаружена. Пока документ
        # недоступен неделю за неделей, повторять это в каждом письме незачем.
        newly = document.check_status != DocumentCheckStatus.UNAVAILABLE.value
        document.check_status = DocumentCheckStatus.UNAVAILABLE.value
        document.check_error = fingerprint.error
        if newly:
            outcome.unavailable.append(DocumentEvent(document, fingerprint.error or "недоступен"))
        return

    document.check_status = DocumentCheckStatus.OK.value
    document.check_error = None

    first_check = not any(
        (document.etag, document.last_modified_header, document.content_hash, document.content_length)
    )
    difference = None if first_check else fingerprint.differs_from(document)

    document.etag = fingerprint.etag
    document.last_modified_header = fingerprint.last_modified
    document.content_length = fingerprint.content_length
    if fingerprint.content_hash:
        document.content_hash = fingerprint.content_hash

    if first_check or difference:
        if fingerprint.last_modified_date:
            document.document_date = fingerprint.last_modified_date
            document.document_date_source = DocumentDateSource.LAST_MODIFIED.value
        else:
            # День фиксации — по МСК: система живёт в московском времени (раздел 8 ТЗ),
            # и «сегодня» ночью по UTC было бы вчерашним числом.
            document.document_date = now_msk().date()
            document.document_date_source = DocumentDateSource.OBSERVED.value

    if difference:
        document.changed_at = now
        document.change_count += 1
        outcome.changed.append(DocumentEvent(document, difference))


def _fetch_fingerprint(url: str) -> Fingerprint:
    """Отпечаток файла по заголовкам; тело читается только когда заголовков недостаточно."""

    try:
        with httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=resolve_verify(url),
        ) as client:
            response = client.head(url)
            # HEAD у части серверов запрещён или отвечает не тем, что GET, — тогда тело
            # всё равно придётся читать.
            if response.status_code >= 400 and response.status_code in (403, 405, 501):
                return _fetch_by_get(client, url)
            if response.status_code >= 400:
                return Fingerprint(error=f"HTTP {response.status_code}")
            fingerprint = _from_headers(response.headers)
            if fingerprint.etag or fingerprint.last_modified or fingerprint.content_length:
                return fingerprint
            return _fetch_by_get(client, url)
    except Exception as exc:  # noqa: BLE001 - один недоступный документ не должен ронять прогон
        return Fingerprint(error=_short_error(exc))


def _fetch_by_get(client: httpx.Client, url: str) -> Fingerprint:
    digest = hashlib.sha256()
    total = 0
    try:
        with client.stream("GET", url) as response:
            if response.status_code >= 400:
                return Fingerprint(error=f"HTTP {response.status_code}")
            fingerprint = _from_headers(response.headers)
            for chunk in response.iter_bytes():
                digest.update(chunk)
                total += len(chunk)
                if total > MAX_HASH_BYTES:
                    return Fingerprint(error=f"файл больше {MAX_HASH_BYTES // (1024 * 1024)} МБ")
    except Exception as exc:  # noqa: BLE001 - см. выше
        return Fingerprint(error=_short_error(exc))
    fingerprint.content_hash = digest.hexdigest()
    if fingerprint.content_length is None:
        fingerprint.content_length = total
    return fingerprint


def _from_headers(headers: httpx.Headers) -> Fingerprint:
    fingerprint = Fingerprint(
        etag=(headers.get("etag") or None),
        last_modified=(headers.get("last-modified") or None),
    )
    length = headers.get("content-length")
    if length and length.isdigit():
        fingerprint.content_length = int(length)
    if fingerprint.last_modified:
        try:
            fingerprint.last_modified_date = parsedate_to_datetime(fingerprint.last_modified).date()
        except (TypeError, ValueError):
            fingerprint.last_modified_date = None
    return fingerprint


def _short_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:300]


# --- Шаг 3: отчёт ---------------------------------------------------------------------


def build_report(outcome: CheckOutcome, db: Session, *, manufacturer: Manufacturer | None) -> tuple[str, str]:
    """Тема и текст письма-отчёта. Текстом, без HTML — как и остальные автоматические
    письма: они собираются из полей справочника, форматировать там нечего."""

    scope = f" — {manufacturer.brand_name or manufacturer.legal_name}" if manufacturer else ""
    subject = (
        f"ORACLE-T: документы по СИ и руководства{scope} — изменилось {len(outcome.changed)}, "
        f"появилось {len(outcome.new)}, пропало {len(outcome.removed)}, "
        f"недоступно {len(outcome.unavailable)}"
    )

    names = _manufacturer_names(db, outcome)
    lines = [
        f"Сверка документов по СИ и руководств по эксплуатации — {now_msk():%d.%m.%Y}.",
        "",
        f"Документов в справочнике: {outcome.registry_size}, проверено с источником: {outcome.checked}.",
        f"Изменилось: {len(outcome.changed)}, появилось: {len(outcome.new)}, "
        f"пропало из справочника: {len(outcome.removed)}, стало недоступно: {len(outcome.unavailable)}.",
    ]
    for heading, events in (
        ("ИЗМЕНИЛИСЬ", outcome.changed),
        ("ПОЯВИЛИСЬ", outcome.new),
        ("ПРОПАЛИ ИЗ СПРАВОЧНИКА", outcome.removed),
        ("СТАЛИ НЕДОСТУПНЫ", outcome.unavailable),
    ):
        if not events:
            continue
        lines += ["", heading]
        for event in events[:REPORT_SECTION_LIMIT]:
            document = event.document
            lines.append(
                f"— {names.get(document.manufacturer_id, '?')} · {document.title}"
            )
            lines.append(f"  {event.detail}")
            lines.append(
                f"  актуальная дата: {_format_date(document.document_date)}"
                f"{_date_source_suffix(document)}"
            )
            lines.append(f"  {document.url}")
        if len(events) > REPORT_SECTION_LIMIT:
            lines.append(f"  … и ещё {len(events) - REPORT_SECTION_LIMIT}")
    lines += [
        "",
        "Полный справочник документов с датами — в разделе «Каталог продукции» ORACLE-T.",
    ]
    return subject, "\n".join(lines)


def _manufacturer_names(db: Session, outcome: CheckOutcome) -> dict[uuid.UUID, str]:
    ids = {
        event.document.manufacturer_id
        for events in (outcome.changed, outcome.new, outcome.removed, outcome.unavailable)
        for event in events
    }
    if not ids:
        return {}
    return {
        m.id: m.brand_name or m.legal_name
        for m in db.scalars(select(Manufacturer).where(Manufacturer.id.in_(ids)))
    }


def _format_date(value: date | None) -> str:
    return f"{value:%d.%m.%Y}" if value else "—"


def _date_source_suffix(document: CatalogDocument) -> str:
    return {
        DocumentDateSource.LAST_MODIFIED.value: " (дата файла на сайте)",
        DocumentDateSource.FGIS_VERSION.value: f" (редакция {document.version_label or '—'} в ФГИС)",
        DocumentDateSource.OBSERVED.value: " (дата фиксации системой)",
    }.get(document.document_date_source or "", "")


# --- Прогон целиком ---------------------------------------------------------------------


def run_check(
    db: Session,
    *,
    manufacturer: Manufacturer | None = None,
    actor_id: uuid.UUID | None = None,
    notify: bool = True,
    gate: RobotsGate | None = None,
) -> CheckOutcome:
    """Синхронизация → сверка → отчёт. Так работает и еженедельная задача планировщика, и
    кнопка в каталоге (та — по одному производителю)."""

    outcome = CheckOutcome()
    sync_registry(db, manufacturer=manufacturer, outcome=outcome)
    check_documents(db, manufacturer=manufacturer, outcome=outcome, gate=gate)

    size_query = select(func.count(CatalogDocument.id)).where(CatalogDocument.is_active.is_(True))
    if manufacturer is not None:
        size_query = size_query.where(CatalogDocument.manufacturer_id == manufacturer.id)
    outcome.registry_size = db.scalar(size_query) or 0

    log_action(
        db,
        component=COMPONENT,
        action="documents_check" + (f":{manufacturer.id}" if manufacturer else ""),
        result="updates" if outcome.has_updates else "no_changes",
        level=LogLevel.INFO,
        details=f"Сверка документов: {outcome.summary()}",
        user_id=actor_id,
    )
    db.commit()

    if notify and outcome.has_updates:
        subject, body = build_report(outcome, db, manufacturer=manufacturer)
        outcome.notification = notification_service.notify_documents_updated(
            db, subject=subject, body=body
        )
    logger.info(f"Сверка документов по СИ и руководств: {outcome.summary()}")
    return outcome


# --- Чтение для интерфейса ---------------------------------------------------------------


def list_documents(db: Session, *, manufacturer_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        select(CatalogDocument, Product.model_name, Product.execution, SiType.si_code)
        .outerjoin(Product, Product.id == CatalogDocument.product_id)
        .outerjoin(SiType, SiType.id == CatalogDocument.si_type_id)
        .where(CatalogDocument.manufacturer_id == manufacturer_id)
        .order_by(
            CatalogDocument.is_active.desc(),
            CatalogDocument.changed_at.desc().nulls_last(),
            Product.model_name,
            SiType.si_code,
            CatalogDocument.kind,
        )
    ).all()
    return [
        {
            **{c.name: getattr(document, c.name) for c in CatalogDocument.__table__.columns},
            "kind_title": KIND_TITLES.get(document.kind, document.kind),
            "model_name": model_name,
            "execution": execution,
            "si_code": si_code,
        }
        for document, model_name, execution, si_code in rows
    ]


def summary(db: Session, *, manufacturer_id: uuid.UUID | None = None) -> dict:
    """Сводка для шапки раздела: сколько документов, когда сверялись, сколько свежих
    изменений. «Свежее» — изменившееся при последней сверке: с той же даты, что и самая
    поздняя проверка."""

    base = select(CatalogDocument).where(CatalogDocument.is_active.is_(True))
    if manufacturer_id is not None:
        base = base.where(CatalogDocument.manufacturer_id == manufacturer_id)
    subquery = base.subquery()
    total, last_checked_at, unavailable = db.execute(
        select(
            func.count(subquery.c.id),
            func.max(subquery.c.last_checked_at),
            func.count(subquery.c.id).filter(
                subquery.c.check_status == DocumentCheckStatus.UNAVAILABLE.value
            ),
        )
    ).one()
    changed_recently = 0
    if last_checked_at is not None:
        changed_recently = (
            db.scalar(
                select(func.count(subquery.c.id)).where(
                    subquery.c.changed_at >= last_checked_at.date()
                )
            )
            or 0
        )
    return {
        "total": total or 0,
        "last_checked_at": last_checked_at,
        "changed_recently": changed_recently,
        "unavailable": unavailable or 0,
    }


# --- Очередь: ручной запуск из каталога ------------------------------------------------


def enqueue_check(
    db: Session, *, manufacturer_id: uuid.UUID | None, actor_id: uuid.UUID | None
) -> CatalogLookupTask | None:
    return catalog_queue_service.enqueue(
        db,
        adapter_key=ADAPTER_KEY,
        reason=CatalogQueueReason.MANUAL,
        manufacturer_id=manufacturer_id,
        actor_id=actor_id,
    )


def handle_task(db: Session, task: CatalogLookupTask) -> TaskOutcome:
    manufacturer = db.get(Manufacturer, task.manufacturer_id) if task.manufacturer_id else None
    # Автора у задачи очереди нет — он остаётся в журнале постановки; сверку выполняет система.
    outcome = run_check(db, manufacturer=manufacturer)
    return TaskOutcome(message=f"Сверка документов: {outcome.summary()}")


def register() -> None:
    catalog_queue_service.register_handler(ADAPTER_KEY, handle_task)
