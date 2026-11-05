"""Отправка событий «Журнала событий» в Bitrix24 (05.11.2026, раздел 84 ARCHITECTURE.md).

События уходят тем же подписанным каналом, что и пакет ИИ-анализа (`bitrix_ai_service`), но
отдельным пакетом: `sections: {}`, `events: [...]`, а актуальные значения — в `deal_fields`
(портал обновляет сделку и пишет историю в журнал). Сделку портал находит по `deal_id`, поэтому
события отправляются только по закупкам, уже выгруженным кнопкой «Загрузить в Битрикс».

Правила отправки:
* события с несогласованным типом (`held`) не отправляются, пока тип не станет согласованным
  в `tender_monitor_service.EVENT_TYPES`;
* технический сбой (нет ответа, 5xx) оставляет события в очереди — уйдут при следующем проходе;
  отказ портала по данным (4xx) переводит события в `error` и сам не повторяется — нужен
  разбор и ручная повторная отправка;
* повторная доставка несёт те же `event_id`, поэтому дубля в журнале портала быть не должно.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bitrix_deal import BitrixAiRequest, BitrixDealLink
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_event import EventDelivery, TenderEvent
from app.services import bitrix_ai_service as bitrix
from app.services.audit import log_action
from app.services.tender_monitor_service import EVENT_TYPES

# Сколько событий в одном пакете: пакет портала ограничен 5 МБ, а события короткие, но
# значения обеспечения и названия документов бывают длинными.
MAX_EVENTS_PER_PACKAGE = 100


def _z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def event_out(event: TenderEvent) -> dict[str, Any]:
    """Событие в формате `events[]` портала; пустые необязательные поля не передаются."""

    data = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "title": event.title,
        "scope": event.scope,
        "lot_number": event.lot_number if event.scope == "LOT" else None,
        "old_value": event.old_value,
        "new_value": event.new_value,
        "detected_at": _z(event.detected_at),
        "effective_at": event.effective_at.isoformat(timespec="seconds") if event.effective_at else None,
        "source_document": event.source_document,
        "source_url": event.source_url,
        "source_version": event.source_version,
        "note": event.note,
        "supersedes_event_id": event.supersedes_event_id,
    }
    return {key: value for key, value in data.items() if value not in (None, "")}


def build_events_payload(
    db: Session, tender: Tender, events: list[TenderEvent], *, request_id: str | None = None
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "schema_version": bitrix.SCHEMA_VERSION,
        "request_id": request_id or f"sova-{uuid.uuid4()}",
        "deal_id": bitrix._deal_id(db, tender),  # noqa: SLF001
        "analysis_id": f"sova-evt-{tender.id.hex[:12]}-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
        "generated_at": _z(now),
        "full_snapshot": False,
        "lookup": bitrix.build_lookup(db, tender),
        "deal_fields": bitrix.build_deal_fields(db, tender),
        "sections": {},
        "events": [event_out(event) for event in events],
    }


def pending_events(db: Session, tender_id: uuid.UUID) -> list[TenderEvent]:
    """События к отправке: ждущие и задержанные, чей тип с тех пор стал согласованным."""

    rows = db.scalars(
        select(TenderEvent)
        .where(
            TenderEvent.tender_id == tender_id,
            TenderEvent.delivery.in_([EventDelivery.PENDING.value, EventDelivery.HELD.value]),
        )
        .order_by(TenderEvent.detected_at, TenderEvent.created_at)
    )
    return [event for event in rows if EVENT_TYPES.get(event.event_type, False)][:MAX_EVENTS_PER_PACKAGE]


def preview(db: Session, tender: Tender) -> dict[str, Any]:
    events = pending_events(db, tender.id)
    payload = build_events_payload(db, tender, events)
    return {"payload": payload, "events_count": len(events)}


def send_pending(
    db: Session,
    tender: Tender,
    *,
    actor_id: uuid.UUID | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Отправляет накопленные события закупки одним пакетом. Возвращает итог для интерфейса:
    `sent`/`skipped`/`error` и пояснение."""

    events = pending_events(db, tender.id)
    if not events:
        return {"status": "empty", "sent": 0, "detail": "Нет событий к отправке."}
    settings = bitrix._settings(db)  # noqa: SLF001
    if not settings.ai_push_enabled:
        return {
            "status": "skipped",
            "sent": 0,
            "detail": "Отправка в Bitrix24 выключена («Приложения → Bitrix24»). События накоплены.",
        }
    if bitrix._deal_id(db, tender) <= 0:  # noqa: SLF001
        return {
            "status": "skipped",
            "sent": 0,
            "detail": "Закупка ещё не выгружена в Bitrix24 — сначала «Загрузить в Битрикс».",
        }

    for event in events:  # задержанные с согласованным теперь типом возвращаются в очередь
        event.delivery = EventDelivery.PENDING.value
    payload = build_events_payload(db, tender, events)
    body = bitrix.serialize(payload)
    if len(body) > bitrix.MAX_BODY_BYTES:
        return {"status": "error", "sent": 0, "detail": "Пакет событий больше предела портала 5 МБ."}

    record = BitrixAiRequest(
        tender_id=tender.id,
        request_id=payload["request_id"],
        analysis_id=payload["analysis_id"],
        deal_id_sent=payload["deal_id"],
        full_snapshot=False,
        items_count=len(events),
        body=body.decode("utf-8"),
        sent_by_id=actor_id,
    )
    db.add(record)
    bitrix._post(settings, record, transport)  # noqa: SLF001

    now = datetime.now(timezone.utc)
    if record.accepted:
        for event in events:
            event.delivery = EventDelivery.SENT.value
            event.request_id = record.request_id
            event.sent_at = now
            event.error = None
        if record.deal_id:
            link = db.get(BitrixDealLink, tender.id) or BitrixDealLink(tender_id=tender.id)
            link.deal_id = record.deal_id
            link.pushed_at = record.sent_at
            link.last_error = None
            db.merge(link)
    else:
        # 4xx — ошибка данных: повтор тем же телом не поможет, автоматически не повторяем.
        data_error = record.http_status is not None and record.http_status < 500
        for event in events:
            event.delivery = EventDelivery.ERROR.value if data_error else EventDelivery.PENDING.value
            event.error = record.error
    log_action(
        db,
        component="monitoring",
        action=f"push_bitrix24_events:{tender.external_id}",
        result="success" if record.accepted else "error",
        level=LogLevel.INFO if record.accepted else LogLevel.WARNING,
        details=(
            f"request_id {record.request_id}, событий {len(events)}"
            + ("" if record.accepted else f": {record.error}")
        ),
        user_id=actor_id,
    )
    db.commit()
    return {
        "status": "sent" if record.accepted else "error",
        "sent": len(events) if record.accepted else 0,
        "detail": None if record.accepted else record.error,
        "request_id": record.request_id,
        "http_status": record.http_status,
    }


def retry_errors(db: Session, tender: Tender) -> int:
    """Возвращает отклонённые порталом события в очередь (после того как причину разобрали)."""

    rows = list(
        db.scalars(
            select(TenderEvent).where(
                TenderEvent.tender_id == tender.id, TenderEvent.delivery == EventDelivery.ERROR.value
            )
        )
    )
    for event in rows:
        event.delivery = EventDelivery.PENDING.value
    db.commit()
    return len(rows)
