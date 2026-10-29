"""Автоматическая сверка моделей каталога с реестрами допуска (замечание 13.10.2026, п.7).

До этой правки записи о допуске заводились только руками (раздел 37 ARCHITECTURE.md), и у
всех моделей стояло «не проверялось». Сервер заказчика в России, а оба реестра закрыты лишь
для зарубежных адресов, — поэтому теперь сервер сам берёт выгрузку
(`app/adapters/admission_registries.py`) и раскладывает её по моделям:

1. **Выгрузка** — по расписанию раз в неделю (ночью: днём ГИСП отвечает минутами) или по
   кнопке. ГИСП опрашивается запросом грида с фильтром по нашим кодам ОКПД2 (коды общих
   профилей отбора, `our_okpd2_codes`), перечень Россетей — PDF со страницы «Проверка
   качества оборудования». Если реестр роботу ничего не отдал, администратор загружает
   файл из браузера (XLSX ГИСП или PDF Россетей), и дальше всё то же самое.
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
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, SiType
from app.models.relevance_profile import RelevanceProfile
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
from app.services.registry_records_service import PP719_MIN_SCORE

COMPONENT = "registry_polling"

# Реестры, которые умеем сверять автоматически. Реестр российского ПО — про встроенное ПО, а
# не про прибор; его записи по-прежнему заводятся вручную.
POLLABLE = (AdmissionRegistry.INDUSTRIAL_PRODUCTS.value, AdmissionRegistry.ROSSETI_ATTESTATION.value)

POLL_RUNNING = "running"

# Строки выгрузки ГИСП, относящиеся к счётчикам электроэнергии, помимо кодов ОКПД2 профилей:
# ОКПД2 26.51.63 (приборы учёта) либо слово «счётчик» рядом с «электр…» в наименовании.
_RELEVANT_RE = re.compile(r"26\.51\.63|сч[её]тчик[^.;]{0,60}электр|электр[^.;]{0,60}сч[её]тчик", re.IGNORECASE)

_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТХаеорсхУу", "ABEKMHOPCTXaeopcxYy")
_LEGAL_FORM_RE = re.compile(r"\b(ООО|АО|ПАО|ЗАО|ОАО|НПК|НПП|НПО|НТЦ)\b|[«»\"']", re.IGNORECASE)


# Если в общих профилях отбора коды ОКПД2 не заданы — приборы учёта электроэнергии.
DEFAULT_OKPD2 = ("26.51.63",)


def our_okpd2_codes(db: Session) -> list[str]:
    """«Наш ОКПД» (замечание 28.10.2026): коды ОКПД2 активных общих профилей отбора — те же,
    по которым отбираются закупки. По ним фильтруется колонка «ОКПД2» реестра ГИСП."""

    codes: set[str] = set()
    for profile_codes in db.scalars(
        select(RelevanceProfile.okpd2_codes).where(
            RelevanceProfile.is_default.is_(True), RelevanceProfile.is_active.is_(True)
        )
    ):
        codes.update(code for code in (profile_codes or []) if code)
    return admission_registries.okpd2_prefixes(codes) or list(DEFAULT_OKPD2)


_WEEKDAYS_PLURAL = ("понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям")


def gisp_timing_hint() -> str:
    """Как и когда опрашивается ГИСП — для карточки модели (замечание 28.10.2026: «укажи это
    в интерфейсе»). Берётся из настроек и констант адаптера, чтобы текст не расходился с
    поведением."""

    settings = get_settings()
    timeout_minutes = round(admission_registries.GISP_TIMEOUT_SECONDS / 60)
    attempts = admission_registries.GISP_ATTEMPTS
    schedule = (
        f"Плановый опрос — по {_WEEKDAYS_PLURAL[settings.registry_poll_weekday % 7]} в "
        f"{settings.registry_poll_time}: днём ГИСП отвечает очень медленно. "
        if settings.registry_poll_enabled
        else "Плановый опрос выключен; днём ГИСП отвечает очень медленно. "
    )
    return f"{schedule}На ответ реестра — до {timeout_minutes} мин, до {attempts} попыток."


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
        # Исключение — бренд в самом наименовании: «МИРТЕК-32-РУ» в реестре ГИСП записан на
        # контрактного производителя ООО «РИМ-РУС» (выгрузка 26.10.2026), и это наш прибор.
        if (
            folded_org
            and keys.manufacturer_tokens
            and not any(t in folded_org for t in keys.manufacturer_tokens)
            and not any(t in _fold(row.product_name) for t in keys.manufacturer_tokens)
        ):
            continue
        found.append(row)
    if not found:
        return None
    # Из нескольких записей — сначала годная для закупок по баллам ПП 719 (совещание
    # 28.10.2026: одна модель бывает в ГИСП и с 80, и со 113 баллами), затем действующая
    # дольше всех; бессрочная — лучше любой.
    return max(
        found,
        key=lambda row: (
            row.score is not None and row.score >= PP719_MIN_SCORE,
            row.valid_to is None,
            row.valid_to or date.min,
        ),
    )


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


def _download(registry: str, *, okpd2_codes: list[str]) -> RegistryFile:
    if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value:
        return admission_registries.fetch_gisp(okpd2_codes)
    return admission_registries.download_rosseti(known_url=ROSSETI_ZAK_URL)


def _parse(registry: str, file: RegistryFile, *, okpd2_codes: list[str]) -> tuple[list[RegistryRow], int]:
    if file.rows is not None:
        return file.rows, file.total
    if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value:
        return admission_registries.parse_xlsx(file.content, relevant=_RELEVANT_RE, okpd2_codes=okpd2_codes)
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
    okpd2_codes = our_okpd2_codes(db)
    try:
        if file is None:
            file = _download(registry, okpd2_codes=okpd2_codes)
        poll.source_url = file.url
        poll.file_name = file.file_name[:255]
        rows, total = _parse(registry, file, okpd2_codes=okpd2_codes)
    except RegistryUnavailable as exc:
        return _finish(db, poll, status=PollStatus.ERROR, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - любой сбой разбора — в журнал опроса, не в 500
        logger.exception(f"Опрос реестра {registry} не удался")
        return _finish(db, poll, status=PollStatus.ERROR, message=f"Сбой разбора выгрузки: {exc}")

    poll.rows_total = total
    poll.rows_relevant = len(rows)
    _apply_rows(db, poll, registry, rows)
    market_note = ""
    if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value:
        # Те же строки — модификациям отчёта «Рынок ПУ» (раздел 79 ARCHITECTURE.md): дата
        # внесения в РПП — точка отсчёта отчёта о локализации. Сбой здесь опрос не роняет.
        try:
            from app.services.meter_market_gisp import apply_gisp_rows

            market = apply_gisp_rows(db, rows)
            market_note = f"; модификаций рынка ПУ найдено {market.matched}" + (
                f", баллы расходятся со справочником аналитика у {market.score_mismatch}" if market.score_mismatch else ""
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Сопоставление ГИСП с модификациями рынка ПУ не удалось: {exc}")
    if registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value:
        label = "реестре ПП 719 (ГИСП)"
        scope = (
            f"Записей по ОКПД2 {', '.join(okpd2_codes)}: {total}"
            if file.rows is not None
            else f"Строк в выгрузке {total}, по ОКПД2 {', '.join(okpd2_codes)} и счётчикам {len(rows)}"
        )
    else:
        label = "перечне ПАО «Россети»"
        scope = f"Записей в перечне {total}"
    return _finish(
        db,
        poll,
        status=PollStatus.OK,
        message=(
            f"{scope}; моделей каталога найдено в {label}: {poll.products_found} из "
            f"{poll.products_checked}, записей обновлено {poll.records_saved}{market_note}"
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
        record.url = row.url or source_url
        record.score = row.score
        record.note = (
            f"Найдено автоматически {now.astimezone().strftime('%d.%m.%Y')} в выгрузке "
            f"«{poll.file_name or 'реестра'}»: {row.product_name[:300]}"
            + (f" — {row.organization[:120]}" if row.organization else "")
            + (f". {row.details[:300]}" if row.details else "")
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
    """Еженедельный опрос по расписанию (ночью — днём ГИСП отвечает минутами): реестры по
    очереди, сбой одного не мешает другому."""

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
