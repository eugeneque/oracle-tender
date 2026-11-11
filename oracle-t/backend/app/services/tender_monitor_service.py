"""Отслеживание изменений по закупкам (05.11.2026, раздел 84 ARCHITECTURE.md).

Bitrix24 принимает изменения по тендерам в «Журнале событий». Чтобы передать «было → стало»,
по каждой закупке под наблюдением хранится слепок прошлой проверки (`tender_monitors.snapshot`);
проверка заново снимает данные с площадки и из карточки ЕИС, строит новый слепок и сравнивает.

Что сравнивается: срок окончания подачи; даты аукциона, переторжки и подведения итогов (из
карточки ЕИС, с поясом); НМЦК; статус; условия обеспечения (поля карточки); состав документов
(новый файл, новая редакция, протокол, разъяснение, договор, ТЗ).

Первая проверка событий не создаёт: она снимает исходный слепок (в документе Bitrix24 —
«при первичной загрузке изменение не выдумывать»).

Чего здесь нет сознательно — см. «Ограничения» в разделе 84: лоты не различаются (всё на
уровне процедуры), документ, заменённый по тому же адресу, не замечается (содержимое не
сверяется), статус «приостановлена» площадки отдельно не отдают.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.registry import get_adapter
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.tender_event import EventDelivery, TenderEvent, TenderMonitor
from app.models.user import User
from app.services import document_service, eis_card_dates, tender_card_service
from app.services.audit import log_action

# Коды событий — по документации Bitrix24 «События СОВА и Bitrix24» (редакция 2026-11-05,
# приложение 1.7.0, schema_version 2): 16 кодов с русскими подписями и два новых для даты итогов
# и общего статуса процедуры. Значение — согласован ли код: событие с несогласованным кодом
# записывается как `held` и не отправляется. Сейчас согласованы все, которые мы формируем.
# Не заменять близкими по смыслу названиями из других документов: у портала именно
# `APPLICATION_END_CHANGED` и `AUCTION_DATE_CHANGED`, а не `DEADLINE_CHANGED`/`AUCTION_CHANGED`.
EVENT_TYPES: dict[str, bool] = {
    "APPLICATION_END_CHANGED": True,
    "AUCTION_DATE_CHANGED": True,
    "REBID_DATE_CHANGED": True,
    "DOCUMENT_ADDED": True,
    "DOCUMENT_REPLACED": True,
    "DOCUMENT_REMOVED": True,
    "TECHNICAL_REQUIREMENT_CHANGED": True,
    "CONTRACT_CHANGED": True,
    "AMOUNT_CHANGED": True,
    "SECURITY_CHANGED": True,
    "CLARIFICATION_PUBLISHED": True,
    "PROCEDURE_SUSPENDED": True,
    "PROCEDURE_RESUMED": True,
    "PROCEDURE_CANCELLED": True,
    "PROTOCOL_PUBLISHED": True,
    "RESULT_CHANGED": True,
    "RESULT_DATE_CHANGED": True,
    "PROCEDURE_STATUS_CHANGED": True,
}

_STATUS_LABELS = {
    "collecting_bids": "приём заявок",
    "evaluation": "рассмотрение заявок",
    "completed": "завершена",
    "cancelled": "отменена",
}

# Пауза между закупками при плановой проверке: каждая — несколько обращений к площадке и ЕИС.
DELAY_BETWEEN_TENDERS_SECONDS = 2.0


# --- слепок -----------------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="minutes")


def _same_instant(left: str | None, right: str | None) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    try:
        return datetime.fromisoformat(left) == datetime.fromisoformat(right)
    except ValueError:
        return False


def _same_price(left: str | None, right: str | None) -> bool:
    try:
        return Decimal(left or "x") == Decimal(right or "x")
    except InvalidOperation:
        return left == right


def _our_result(db: Session, tender: Tender) -> str | None:
    from app.services.protocol_results_service import our_result

    return our_result(db, tender)


def build_snapshot(db: Session, tender: Tender) -> dict[str, Any]:
    """Слепок наблюдаемых показателей закупки в виде, пригодном для сравнения и для JSONB."""

    card = db.get(TenderCard, tender.id)
    payload = card.payload if card is not None else None
    dates = eis_card_dates.dates_from_payload(payload)
    # Срок подачи — по карточке ЕИС (точное время и пояс), иначе по выдаче площадки.
    fields = {
        "application_end": _iso(dates.get("application_end") or tender.application_end),
        "auction_date": _iso(dates.get("auction_date")),
        "rebid_date": _iso(dates.get("rebid_date")),
        "result_date": _iso(dates.get("result_date")),
        "price": str(tender.price) if tender.price is not None else None,
        "currency": tender.currency,
        "status": tender.status,
        # Результат нашей заявки по протоколам (раздел 96): None — нас в протоколах нет.
        "our_result": _our_result(db, tender),
    }
    security = {
        name: value.strip()[:500]
        for name, value in eis_card_dates._fields(payload)  # noqa: SLF001 - тот же разбор полей карточки
        if "обеспечени" in name and value.strip()
    }
    documents = {
        doc.source_url: {"name": doc.file_name, "class": doc.document_class}
        for doc in document_service.list_tender_documents(db, tender.id)
    }
    return {"fields": fields, "security": security, "documents": documents}


# --- сравнение --------------------------------------------------------------------------------


def _change(
    event_type: str,
    field_key: str,
    title: str,
    old: str | None,
    new: str | None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "field_key": field_key,
        "title": title,
        "old_value": old,
        "new_value": new,
        **extra,
    }


_NO_PREVIOUS = "Прежнего значения нет: показатель обнаружен впервые."
_RESULT_TITLES = {
    "WIN": "Опубликована наша победа",
    "LOSS": "Опубликован итог: победил другой участник",
    "NOT_ADMITTED": "Наша заявка не допущена",
    "PENDING": "Рассмотрение нашей заявки продолжается",
}


def _security_kind(name: str) -> str | None:
    """Вид обеспечения по названию поля карточки: заявки или исполнения контракта."""

    lowered = name.lower()
    if "заяв" in lowered:
        return "APPLICATION"
    if any(word in lowered for word in ("исполнен", "контракт", "договор")):
        return "CONTRACT"
    return None


def _document_event(url: str, doc: dict[str, Any], old_docs: dict[str, Any]) -> dict[str, Any]:
    name = doc.get("name") or "документ"
    klass = doc.get("class")
    old_names = {d.get("name") for d in old_docs.values()}
    had_class = any(d.get("class") == klass for d in old_docs.values())
    revised = name in old_names
    lowered = name.lower()
    common = {"source_document": name, "source_url": url}
    # Файл с тем же названием, но другим адресом — вероятно, новая редакция; номер версии не
    # выдумываем (требование Bitrix24).
    revision_note = "Файл с тем же названием, но другим адресом — вероятно, новая редакция." if revised else None

    if klass == DocumentClass.PROTOCOL.value:
        return _change(
            "PROTOCOL_PUBLISHED", f"doc:{url}", f"Опубликован протокол: {name}", None, name,
            data={"protocol_kind": name, "is_final": True if "итогов" in lowered else None},
            note="Итоговый характер определён по названию файла." if "итогов" in lowered
            else "Итоговый ли протокол, по названию файла не определить.",
            **common,
        )
    if "разъяснен" in lowered:
        return _change(
            "CLARIFICATION_PUBLISHED", f"doc:{url}", f"Опубликовано разъяснение: {name}", None, name,
            note="В значении — название файла; содержание разъяснения в самом документе.",
            **common,
        )
    if klass == DocumentClass.CONTRACT.value and had_class:
        return _change(
            "CONTRACT_CHANGED", f"doc:{url}", f"Новая редакция проекта договора: {name}",
            name if revised else None, name,
            note=revision_note or "Опубликован ещё один файл договора; различия пунктов не сверялись.",
            **common,
        )
    if klass == DocumentClass.TZ_DESCRIPTION.value and had_class:
        return _change(
            "TECHNICAL_REQUIREMENT_CHANGED", f"doc:{url}", f"Новая редакция ТЗ: {name}",
            name if revised else None, name,
            note=revision_note or "Опубликован ещё один файл ТЗ; изменённые требования не сверялись.",
            **common,
        )
    if revised:
        return _change(
            "DOCUMENT_REPLACED", f"doc:{url}", f"Заменён документ: {name}", name, name,
            note=revision_note, **common,
        )
    return _change(
        "DOCUMENT_ADDED", f"doc:{url}", f"Добавлен документ: {name}", None, name,
        note="Прежнего значения нет: документ опубликован впервые.", **common,
    )


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Изменения между двумя слепками. Чистая функция: без базы и сети."""

    changes: list[dict[str, Any]] = []
    of, nf = old.get("fields") or {}, new.get("fields") or {}

    for field, code, title_first, title_changed in (
        ("application_end", "APPLICATION_END_CHANGED", "Назначен срок окончания подачи заявок",
         "Изменился срок окончания подачи заявок"),
        # Дата пропала из карточки — не событие: чаще это сбой разбора страницы, чем снятие даты.
        ("auction_date", "AUCTION_DATE_CHANGED", "Назначена дата аукциона", "Изменилась дата аукциона"),
        ("rebid_date", "REBID_DATE_CHANGED", "Назначена переторжка", "Изменилась дата переторжки"),
        ("result_date", "RESULT_DATE_CHANGED", "Назначена дата подведения итогов",
         "Изменилась дата подведения итогов"),
    ):
        if nf.get(field) and not _same_instant(of.get(field), nf[field]):
            first = not of.get(field)
            changes.append(
                _change(
                    code, field, title_first if first else title_changed, of.get(field), nf[field],
                    note=_NO_PREVIOUS if first else None,
                )
            )

    if nf.get("price") is not None and not _same_price(of.get("price"), nf["price"]):
        changes.append(
            _change(
                "AMOUNT_CHANGED", "price", "Изменилась НМЦК", of.get("price"), nf["price"],
                data={"currency": nf.get("currency") or "RUB"},
                note=_NO_PREVIOUS if of.get("price") is None else None,
            )
        )

    old_status, new_status = of.get("status"), nf.get("status")
    if new_status and new_status != old_status:
        cancelled = new_status == "cancelled"
        changes.append(
            _change(
                "PROCEDURE_CANCELLED" if cancelled else "PROCEDURE_STATUS_CHANGED",
                "status",
                "Процедура отменена" if cancelled else "Изменился статус процедуры",
                _STATUS_LABELS.get(old_status, old_status),
                _STATUS_LABELS.get(new_status, new_status),
                note="Статус — по классификации СОВА, а не дословная формулировка площадки.",
            )
        )

    # Результат нашей заявки (RESULT_CHANGED, пример `events_06` Bitrix24). Пропавший результат —
    # не событие (как и пропавшая дата): «нет нас в протоколах» не отменяет прежний итог.
    if nf.get("our_result") and nf["our_result"] != of.get("our_result"):
        changes.append(
            _change(
                "RESULT_CHANGED",
                "our_result",
                _RESULT_TITLES.get(nf["our_result"], "Изменился результат нашей заявки"),
                of.get("our_result"),
                nf["our_result"],
                source_document="Протоколы закупки",
                note="Определено разбором протоколов по ИНН наших компаний; итог проверяет тендерный отдел.",
            )
        )

    old_sec, new_sec = old.get("security") or {}, new.get("security") or {}
    for kind in ("APPLICATION", "CONTRACT", None):
        o = {k: v for k, v in old_sec.items() if _security_kind(k) == kind}
        n = {k: v for k, v in new_sec.items() if _security_kind(k) == kind}
        if not n or o == n:
            continue

        def render(data: dict[str, str]) -> str | None:
            return "\n".join(f"{k}: {v}" for k, v in sorted(data.items())) or None

        label = {"APPLICATION": "заявки", "CONTRACT": "исполнения договора", None: ""}[kind]
        changes.append(
            _change(
                "SECURITY_CHANGED",
                f"security:{kind or 'OTHER'}",
                f"Изменились условия обеспечения {label}".strip(),
                render(o),
                render(n),
                data={"security_kind": kind} if kind else {},
                note=None if o else _NO_PREVIOUS,
            )
        )

    old_docs, new_docs = old.get("documents") or {}, new.get("documents") or {}
    for url, doc in new_docs.items():
        if url not in old_docs:
            changes.append(_document_event(url, doc, old_docs))
    # `DOCUMENT_REMOVED` намеренно не формируем: Bitrix24 просит передавать его только при
    # подтверждённом удалении, а пропажа файла из выдачи площадки чаще означает сбой страницы.
    return changes


# --- повторный сбор данных --------------------------------------------------------------------


def _refresh_fields(db: Session, tender: Tender) -> None:
    """Поля закупки со страницы площадки (цена, статус, сроки) — как при плановом опросе, но
    только для этой закупки, а не поиском по выдаче."""

    from app.services.tender_service import _UPDATABLE_FIELDS

    adapter = get_adapter(tender.source.adapter_key)
    if adapter is None:
        return
    details = adapter.get_tender_details(tender.external_id)
    for name in _UPDATABLE_FIELDS:
        value = getattr(details, name, None)
        if value is None or getattr(tender, name) == value:
            continue
        if eis_card_dates.keep_card_deadline(db, tender, name, value):
            continue
        setattr(tender, name, value)
    db.add(tender)


def _refresh_documents(db: Session, tender: Tender) -> None:
    """Дописывает документы, которых ещё нет у закупки. Уже сохранённые не трогаются."""

    existing = document_service.list_tender_documents(db, tender.id)
    if not existing:
        document_service.sync_tender_documents_with_status(db, tender)
        return
    adapter = get_adapter(tender.source.adapter_key)
    if adapter is None:
        return
    refs = adapter.download_documents(tender.external_id, tender.source_url)
    known = {doc.source_url for doc in existing}
    tender_dir = document_service.get_storage_root() / str(tender.id)
    tender_dir.mkdir(parents=True, exist_ok=True)
    for ref in refs:
        if ref.url in known:
            continue
        known.add(ref.url)
        doc = TenderDocument(
            tender_id=tender.id,
            file_name=ref.file_name,
            file_type=document_service._guess_extension(ref.file_name, ref.url),  # noqa: SLF001
            source_url=ref.url,
            document_class=document_service.classify_document(ref.file_name),
        )
        try:
            with db.begin_nested():
                db.add(doc)
                db.flush()
        except IntegrityError:
            continue
        document_service._process_document(doc, tender_dir)  # noqa: SLF001
    db.commit()


def _refresh_protocols(db: Session, tender: Tender) -> None:
    from app.services.protocol_results_service import refresh_if_new

    refresh_if_new(db, tender)


def refresh_tender(db: Session, tender: Tender) -> list[str]:
    """Заново собирает данные закупки. Сбой одного источника не отменяет остальные —
    возвращает тексты ошибок."""

    errors: list[str] = []
    for label, step in (
        ("страница площадки", lambda: _refresh_fields(db, tender)),
        ("карточка ЕИС", lambda: tender_card_service.sync_card(db, tender, force=True)),
        ("документы", lambda: _refresh_documents(db, tender)),
        # Новый протокол — сразу разбор: иначе слепок не увидит смену нашего результата до
        # следующего ручного анализа. Модель вызывается только при новом файле протокола.
        ("протоколы", lambda: _refresh_protocols(db, tender)),
    ):
        try:
            step()
            db.commit()
        except Exception as exc:  # noqa: BLE001 - изоляция сбоя одного источника
            db.rollback()
            errors.append(f"{label}: {exc}")
            logger.warning(f"Отслеживание {tender.external_id}: {label} не обновлено — {exc}")
    return errors


# --- проверка ---------------------------------------------------------------------------------


def get_monitor(db: Session, tender_id: uuid.UUID) -> TenderMonitor | None:
    return db.get(TenderMonitor, tender_id)


def enable(db: Session, tender: Tender, *, actor: User | None) -> TenderMonitor:
    """Ставит закупку под наблюдение и снимает исходный слепок (если его ещё нет)."""

    monitor = get_monitor(db, tender.id)
    if monitor is None:
        monitor = TenderMonitor(tender_id=tender.id, started_by_id=actor.id if actor else None)
        db.add(monitor)
    monitor.enabled = True
    db.commit()
    check_tender(db, tender)
    log_action(
        db,
        component="monitoring",
        action=f"enable_monitoring:{tender.external_id}",
        result="success",
        user_id=actor.id if actor else None,
    )
    db.commit()
    db.refresh(monitor)
    return monitor


def disable(db: Session, tender: Tender, *, actor: User | None) -> TenderMonitor | None:
    monitor = get_monitor(db, tender.id)
    if monitor is not None:
        monitor.enabled = False
        log_action(
            db,
            component="monitoring",
            action=f"disable_monitoring:{tender.external_id}",
            result="success",
            user_id=actor.id if actor else None,
        )
        db.commit()
    return monitor


def _last_event_id(db: Session, tender_id: uuid.UUID, field_key: str) -> str | None:
    return db.scalar(
        select(TenderEvent.event_id)
        .where(TenderEvent.tender_id == tender_id, TenderEvent.field_key == field_key)
        .order_by(TenderEvent.detected_at.desc(), TenderEvent.created_at.desc())
        .limit(1)
    )


def record_events(
    db: Session, tender: Tender, changes: list[dict[str, Any]], *, detected_at: datetime | None = None
) -> list[TenderEvent]:
    detected_at = detected_at or datetime.now(timezone.utc)
    events: list[TenderEvent] = []
    for change in changes:
        event_type = change["event_type"]
        agreed = EVENT_TYPES.get(event_type, False)
        event = TenderEvent(
            tender_id=tender.id,
            event_id=f"sova-evt-{uuid.uuid4().hex}",
            event_type=event_type,
            field_key=change["field_key"],
            title=change["title"][:300],
            scope="PROCEDURE",
            old_value=change.get("old_value"),
            new_value=change.get("new_value"),
            detected_at=detected_at,
            data=change.get("data") or None,
            source_document=(change.get("source_document") or None),
            source_url=change.get("source_url") or tender.source_url,
            note=change.get("note"),
            supersedes_event_id=_last_event_id(db, tender.id, change["field_key"]),
            delivery=EventDelivery.PENDING.value if agreed else EventDelivery.HELD.value,
        )
        db.add(event)
        db.flush()
        events.append(event)
    return events


def check_tender(db: Session, tender: Tender, *, refresh: bool = True) -> list[TenderEvent]:
    """Одна проверка закупки: сбор → слепок → сравнение → события. Первая проверка только
    снимает слепок."""

    monitor = get_monitor(db, tender.id)
    if monitor is None:
        raise LookupError("Закупка не поставлена под наблюдение")
    errors = refresh_tender(db, tender) if refresh else []
    new_snapshot = build_snapshot(db, tender)
    events: list[TenderEvent] = []
    now = datetime.now(timezone.utc)
    if monitor.snapshot:
        events = record_events(db, tender, diff_snapshots(monitor.snapshot, new_snapshot), detected_at=now)
    else:
        monitor.snapshot_at = now
    monitor.snapshot = new_snapshot
    monitor.last_checked_at = now
    monitor.checks_count = (monitor.checks_count or 0) + 1
    monitor.last_error = "; ".join(errors)[:2000] if errors else None
    db.add(monitor)
    if events:
        log_action(
            db,
            component="monitoring",
            action=f"check_tender:{tender.external_id}",
            result="success",
            level=LogLevel.INFO,
            details="; ".join(f"{e.event_type}: {e.title}" for e in events)[:1500],
        )
    db.commit()
    return events


def run_all(db: Session, *, delay: float = DELAY_BETWEEN_TENDERS_SECONDS) -> dict[str, int]:
    """Плановый проход: все закупки под наблюдением, затем отправка накопленных событий."""

    from app.services import tender_event_service

    monitors = list(db.scalars(select(TenderMonitor).where(TenderMonitor.enabled.is_(True))))
    checked = new_events = failed = 0
    for index, monitor in enumerate(monitors):
        tender = db.get(Tender, monitor.tender_id)
        if tender is None:
            continue
        if index:
            time.sleep(delay)
        try:
            new_events += len(check_tender(db, tender))
            checked += 1
        except Exception as exc:  # noqa: BLE001 - одна закупка не должна останавливать остальные
            db.rollback()
            failed += 1
            logger.warning(f"Проверка закупки {tender.external_id} не удалась: {exc}")
        try:
            tender_event_service.send_pending(db, tender)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning(f"Отправка событий {tender.external_id} не удалась: {exc}")
    return {"checked": checked, "events": new_events, "failed": failed}
