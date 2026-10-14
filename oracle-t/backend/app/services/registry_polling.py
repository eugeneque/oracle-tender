"""Автоматическая сверка моделей каталога с реестрами допуска (замечание 13.10.2026, п.7).

До этой правки записи о допуске заводились только руками (раздел 37 ARCHITECTURE.md), и у
всех моделей стояло «не проверялось». Сервер заказчика в России, а оба реестра закрыты лишь
для зарубежных адресов, — поэтому теперь сервер сам берёт выгрузку
(`app/adapters/admission_registries.py`) и раскладывает её по моделям:

1. **Выгрузка** — скачивается по расписанию раз в неделю или по кнопке; если реестр её
   роботу не отдал, администратор загружает файл из браузера, и дальше всё то же самое.
2. **Сопоставление** — модель ищется в строке выгрузки по обозначению типа («CE210»,
   «МИРТЕК-12-РУ») с границами слова, а у ГИСП ещё и по производителю в колонке организации:
   обозначения вроде «СЕ102» без производителя слишком коротки, чтобы им верить.
3. **Запись** — найденное ложится в `product_registry_records` с отметкой `auto_found_at` и
   без «сверено человеком»: ПП 719 по правилу тендерного отдела всё равно проверяется
   вручную (баллы, год, исполнения). Записи, заведённые или поправленные человеком, опрос не
   трогает. Если модель пропала из очередной выгрузки, автоматическая запись снимается —
   модель возвращается в «нет сведений», а не получает «записи нет»: несовпадение
   обозначения не доказывает, что прибора в реестре нет.
4. **Журнал** — каждый опрос пишется в `admission_registry_polls`: карточка модели
   показывает «реестр опрошен 12.10, модель не найдена — сверьте вручную» вместо
   безликого «не проверялось».
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.adapters import admission_registries
from app.adapters.admission_registries import RegistryFile, RegistryRow, RegistryUnavailable
from app.db.session import SessionLocal
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, SiType
from app.models.registry_record import (
    AdmissionRegistry,
    AdmissionRegistryPoll,
    PollMethod,
    PollStatus,
    ProductRegistryRecord,
    RegistryPresence,
)
from app.models.user import User
from app.seed.meter_parameters import ROSSETI_ZAK_URL
from app.services.audit import log_action

COMPONENT = "registry_polling"

# Реестры, которые умеем сверять автоматически. Реестр российского ПО — про встроенное ПО, а
# не про прибор; его записи по-прежнему заводятся вручную.
POLLABLE = (AdmissionRegistry.INDUSTRIAL_PRODUCTS.value, AdmissionRegistry.ROSSETI_ATTESTATION.value)

POLL_RUNNING = "running"

# Строки выгрузки ГИСП, относящиеся к счётчикам электроэнергии: ОКПД2 26.51.63 (приборы
# учёта) либо слово «счётчик» рядом с «электр…» в наименовании.
_RELEVANT_RE = re.compile(r"26\.51\.63|сч[её]тчик[^.;]{0,60}электр|электр[^.;]{0,60}сч[её]тчик", re.IGNORECASE)

_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТХаеорсхУу", "ABEKMHOPCTXaeopcxYy")
_LEGAL_FORM_RE = re.compile(r"\b(ООО|АО|ПАО|ЗАО|ОАО|НПК|НПП|НПО|НТЦ)\b|[«»\"']", re.IGNORECASE)


def _fold(text: str | None) -> str:
    return (text or "").translate(_HOMOGLYPHS).upper().replace("Ё", "Е")


@dataclass
class _ProductKeys:
    product: Product
    patterns: list[re.Pattern[str]]
    manufacturer_tokens: list[str]


def _designation_pattern(designation: str) -> re.Pattern[str] | None:
    """Обозначение → регулярное выражение с границами: «CE210» не должно находиться внутри
    «CE2102», а «МИРТЕК-12-РУ» — совпадать и с «МИРТЕК 12 РУ»."""

    folded = _fold(designation).strip(" «»\"'")
    if len(folded) < 4 or not re.search(r"\d", folded):
        return None
    parts = [re.escape(part) for part in re.split(r"[\s\-‐‑–.]+", folded) if part]
    body = r"[\s\-‐‑–.]?".join(parts)
    return re.compile(rf"(?<![0-9A-ZА-Я]){body}(?![0-9])")


def _manufacturer_tokens(manufacturer: Manufacturer) -> list[str]:
    tokens: list[str] = []
    for name in (manufacturer.brand_name, manufacturer.legal_name):
        # «Метроника (АЛЬФА)» → «МЕТРОНИКА»: в скобках линейка, а не организация.
        cleaned = re.sub(r"\(.*?\)", " ", _LEGAL_FORM_RE.sub(" ", name or ""))
        for word in _fold(cleaned).split():
            if len(word) >= 4 and word not in tokens:
                tokens.append(word)
    return tokens


def _product_keys(db: Session) -> list[_ProductKeys]:
    manufacturers = {m.id: m for m in db.scalars(select(Manufacturer))}
    si_types = {s.id: s for s in db.scalars(select(SiType))}
    keys: list[_ProductKeys] = []
    for product in db.scalars(select(Product)):
        designations = [product.model_code]
        si_type = si_types.get(product.si_type_id) if product.si_type_id else None
        if si_type and si_type.notation:
            designations.append(si_type.notation)
        patterns = [p for p in (_designation_pattern(d) for d in designations if d) if p]
        if not patterns:
            continue
        manufacturer = manufacturers.get(product.manufacturer_id)
        keys.append(
            _ProductKeys(
                product=product,
                patterns=patterns,
                manufacturer_tokens=_manufacturer_tokens(manufacturer) if manufacturer else [],
            )
        )
    return keys


def _best_row(keys: _ProductKeys, rows: list[tuple[RegistryRow, str, str]]) -> RegistryRow | None:
    found: list[RegistryRow] = []
    for row, folded_raw, folded_org in rows:
        if not any(pattern.search(folded_raw) for pattern in keys.patterns):
            continue
        # У ГИСП есть колонка организации: обозначение чужого производителя не засчитывается.
        if folded_org and keys.manufacturer_tokens and not any(t in folded_org for t in keys.manufacturer_tokens):
            continue
        found.append(row)
    if not found:
        return None
    # Из нескольких записей — действующая дольше всех; бессрочная — лучше любой.
    return max(found, key=lambda row: (row.valid_to is None, row.valid_to or date.min))


# --------------------------------------------------------------------------- опрос


def latest_polls(db: Session) -> dict[str, dict]:
    """Последний опрос и последний успешный — по каждому реестру."""

    result: dict[str, dict] = {}
    for registry in POLLABLE:
        last = db.scalar(
            select(AdmissionRegistryPoll)
            .where(AdmissionRegistryPoll.registry == registry)
            .order_by(AdmissionRegistryPoll.started_at.desc())
            .limit(1)
        )
        last_ok = db.scalar(
            select(AdmissionRegistryPoll)
            .where(
                AdmissionRegistryPoll.registry == registry,
                AdmissionRegistryPoll.status == PollStatus.OK.value,
            )
            .order_by(AdmissionRegistryPoll.started_at.desc())
            .limit(1)
        )
        result[registry] = {"last": last, "last_ok": last_ok}
    return result


def _start(db: Session, registry: str, method: PollMethod, actor_id: uuid.UUID | None) -> AdmissionRegistryPoll:
    if registry not in POLLABLE:
        raise ValueError("Этот реестр автоматически не сверяется — записи заводятся вручную")
    poll = AdmissionRegistryPoll(
        registry=registry, method=method.value, status=POLL_RUNNING, user_id=actor_id
    )
    db.add(poll)
    db.commit()
    db.refresh(poll)
    return poll


def _finish(db: Session, poll: AdmissionRegistryPoll, *, status: PollStatus, message: str) -> AdmissionRegistryPoll:
    poll.status = status.value
    poll.message = message
    poll.finished_at = datetime.now(timezone.utc)
    log_action(
        db,
        component=COMPONENT,
        action=f"poll:{poll.registry}:{poll.method}",
        result="success" if status is PollStatus.OK else "error",
        level=LogLevel.INFO if status is PollStatus.OK else LogLevel.WARNING,
        details=message,
        user_id=poll.user_id,
    )
    db.commit()
    db.refresh(poll)
    return poll


def _download(registry: str) -> RegistryFile:
    if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value:
        return admission_registries.download_gisp()
    return admission_registries.download_rosseti(known_url=ROSSETI_ZAK_URL)


def _parse(registry: str, file: RegistryFile) -> tuple[list[RegistryRow], int]:
    if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value:
        return admission_registries.parse_xlsx(file.content, relevant=_RELEVANT_RE)
    return admission_registries.parse_pdf(file.content)


def run_poll(
    db: Session,
    registry: str,
    *,
    method: PollMethod,
    file: RegistryFile | None = None,
    actor_id: uuid.UUID | None = None,
    poll: AdmissionRegistryPoll | None = None,
) -> AdmissionRegistryPoll:
    """Один опрос: скачать (или взять загруженный файл), разобрать, разложить по моделям."""

    poll = poll or _start(db, registry, method, actor_id)
    try:
        if file is None:
            file = _download(registry)
        poll.source_url = file.url
        poll.file_name = file.file_name[:255]
        rows, total = _parse(registry, file)
    except RegistryUnavailable as exc:
        return _finish(db, poll, status=PollStatus.ERROR, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - любой сбой разбора — в журнал опроса, не в 500
        logger.exception(f"Опрос реестра {registry} не удался")
        return _finish(db, poll, status=PollStatus.ERROR, message=f"Сбой разбора выгрузки: {exc}")

    poll.rows_total = total
    poll.rows_relevant = len(rows)
    _apply_rows(db, poll, registry, rows)
    label = (
        "реестре ПП 719 (ГИСП)"
        if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value
        else "перечне ПАО «Россети»"
    )
    return _finish(
        db,
        poll,
        status=PollStatus.OK,
        message=(
            f"Строк в выгрузке {total}, по счётчикам электроэнергии {len(rows)}; моделей "
            f"каталога найдено в {label}: {poll.products_found} из {poll.products_checked}, "
            f"записей обновлено {poll.records_saved}"
        ),
    )


def _apply_rows(db: Session, poll: AdmissionRegistryPoll, registry: str, rows: list[RegistryRow]) -> None:
    prepared = [(row, _fold(row.raw or row.product_name), _fold(row.organization)) for row in rows]
    now = datetime.now(timezone.utc)
    records = {
        record.product_id: record
        for record in db.scalars(select(ProductRegistryRecord).where(ProductRegistryRecord.registry == registry))
    }
    source_url = poll.source_url or (
        admission_registries.GISP_PP719_PAGE
        if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value
        else ROSSETI_ZAK_URL
    )
    stale: list[uuid.UUID] = []
    for keys in _product_keys(db):
        poll.products_checked += 1
        record = records.get(keys.product.id)
        manual = record is not None and record.auto_found_at is None
        row = _best_row(keys, prepared)
        if row is None:
            if record is not None and not manual:
                stale.append(record.id)
            continue
        poll.products_found += 1
        if manual:
            # Запись человека важнее автоматики — и при совпадении, и при расхождении.
            continue
        if record is None:
            record = ProductRegistryRecord(product_id=keys.product.id, registry=registry)
            db.add(record)
        record.presence = RegistryPresence.PRESENT.value
        record.record_number = (row.record_number or "")[:100] or None
        record.issued_at = row.issued_at
        record.valid_to = row.valid_to
        record.url = source_url
        record.note = (
            f"Найдено автоматически {now.astimezone().strftime('%d.%m.%Y')} в выгрузке "
            f"«{poll.file_name or 'реестра'}»: {row.product_name[:300]}"
            + (f" — {row.organization[:120]}" if row.organization else "")
        )
        record.verified_by_user = False
        record.verified_at = None
        record.auto_found_at = now
        record.updated_by_id = None
        poll.records_saved += 1
    if stale:
        db.execute(delete(ProductRegistryRecord).where(ProductRegistryRecord.id.in_(stale)))
        poll.records_saved += len(stale)
    db.flush()


def poll_in_background(registry: str, *, actor_id: uuid.UUID | None) -> AdmissionRegistryPoll:
    """Опрос по кнопке. Выгрузка ГИСП — десятки мегабайт, поэтому ответ возвращается сразу
    с записью «идёт опрос», а сам опрос идёт в отдельном потоке (не в очереди справочника:
    там может часами идти обход каталогов)."""

    db = SessionLocal()
    try:
        poll = _start(db, registry, PollMethod.DOWNLOAD, actor_id)
        poll_id = poll.id
        db.expunge(poll)
    finally:
        db.close()

    def work() -> None:
        session = SessionLocal()
        try:
            run_poll(
                session,
                registry,
                method=PollMethod.DOWNLOAD,
                actor_id=actor_id,
                poll=session.get(AdmissionRegistryPoll, poll_id),
            )
        except Exception as exc:  # noqa: BLE001 - поток не должен падать молча
            logger.exception(f"Фоновый опрос реестра {registry}: {exc}")
        finally:
            session.close()

    threading.Thread(target=work, name=f"registry-poll-{registry}", daemon=True).start()
    return poll


def poll_all_scheduled() -> None:
    """Еженедельный опрос по расписанию: реестры по очереди, сбой одного не мешает другому."""

    db = SessionLocal()
    try:
        for registry in POLLABLE:
            poll = run_poll(db, registry, method=PollMethod.DOWNLOAD)
            logger.info(f"Реестр {registry}: {poll.status} — {poll.message}")
    finally:
        db.close()


def poll_uploaded_file(
    db: Session, registry: str, *, content: bytes, file_name: str, actor: User
) -> AdmissionRegistryPoll:
    """Файл выгрузки, скачанный человеком из браузера, — когда реестр не отдал его роботу."""

    return run_poll(
        db,
        registry,
        method=PollMethod.UPLOAD,
        file=RegistryFile(content=content, file_name=file_name or "выгрузка", url=None),
        actor_id=actor.id,
    )


def mark_interrupted(db: Session) -> None:
    """После перезапуска сервера «идёт опрос» без конца не должен висеть в интерфейсе."""

    for poll in db.scalars(select(AdmissionRegistryPoll).where(AdmissionRegistryPoll.status == POLL_RUNNING)):
        poll.status = PollStatus.ERROR.value
        poll.message = "Опрос прерван перезапуском сервера — запустите снова"
        poll.finished_at = datetime.now(timezone.utc)
    db.commit()
