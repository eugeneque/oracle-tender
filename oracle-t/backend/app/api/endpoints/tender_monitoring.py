"""Отслеживание изменений по закупкам и журнал событий Bitrix24 (раздел 84 ARCHITECTURE.md)."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tender import Tender
from app.models.tender_event import EventDelivery, TenderEvent, TenderMonitor
from app.models.user import User
from app.services import bitrix_ai_service, tender_event_service, tender_monitor_service

router = APIRouter(tags=["monitoring"])


class MonitoringToggle(BaseModel):
    enabled: bool


class EventOut(BaseModel):
    event_id: str
    event_type: str
    title: str
    old_value: str | None
    new_value: str | None
    detected_at: datetime
    delivery: str
    type_agreed: bool
    error: str | None
    sent_at: datetime | None
    # Обнаружено после того, как пользователь последний раз открывал журнал (зелёная точка).
    is_new: bool = False
    # Может повлиять на разбор ИИ (раздел 100).
    affects_analysis: bool = False


class MonitoringState(BaseModel):
    enabled: bool
    # Поставлено системой при выгрузке в Bitrix24, а не вручную (раздел 100).
    auto: bool = False
    baseline_at: datetime | None
    last_checked_at: datetime | None
    checks_count: int
    last_error: str | None
    has_deal: bool
    events: list[EventOut]
    send_result: dict[str, Any] | None = None


class MonitoredTender(BaseModel):
    tender_id: uuid.UUID
    external_id: str
    title: str
    last_checked_at: datetime | None
    events_total: int
    events_pending: int
    events_held: int
    events_error: int


def _tender(db: Session, tender_id: uuid.UUID) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return tender


def _state(
    db: Session, tender: Tender, user: User, send_result: dict[str, Any] | None = None
) -> MonitoringState:
    monitor = tender_monitor_service.get_monitor(db, tender.id)
    seen = tender_monitor_service.seen_at(db, user, tender.id)
    cutoff = tender_monitor_service._unseen_cutoff()  # noqa: SLF001
    events = list(
        db.scalars(
            select(TenderEvent)
            .where(TenderEvent.tender_id == tender.id)
            .order_by(TenderEvent.detected_at.desc(), TenderEvent.created_at.desc())
            .limit(100)
        )
    )
    return MonitoringState(
        enabled=bool(monitor and monitor.enabled),
        auto=bool(monitor and monitor.auto),
        baseline_at=monitor.snapshot_at if monitor else None,
        last_checked_at=monitor.last_checked_at if monitor else None,
        checks_count=monitor.checks_count if monitor else 0,
        last_error=monitor.last_error if monitor else None,
        has_deal=bitrix_ai_service._deal_id(db, tender) > 0,  # noqa: SLF001
        events=[
            EventOut(
                event_id=e.event_id,
                event_type=e.event_type,
                title=e.title,
                old_value=e.old_value,
                new_value=e.new_value,
                detected_at=e.detected_at,
                delivery=e.delivery,
                type_agreed=tender_monitor_service.EVENT_TYPES.get(e.event_type, False),
                error=e.error,
                sent_at=e.sent_at,
                is_new=e.created_at >= cutoff and (seen is None or e.created_at > seen),
                affects_analysis=e.event_type in tender_monitor_service.ANALYSIS_AFFECTING_EVENTS,
            )
            for e in events
        ],
        send_result=send_result,
    )


@router.get("/monitoring", response_model=list[MonitoredTender])
def list_monitored(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[MonitoredTender]:
    """Все закупки под наблюдением со счётчиками событий."""

    monitors = list(
        db.scalars(
            select(TenderMonitor).where(TenderMonitor.enabled.is_(True)).order_by(TenderMonitor.created_at)
        )
    )
    result: list[MonitoredTender] = []
    for monitor in monitors:
        tender = db.get(Tender, monitor.tender_id)
        if tender is None:
            continue
        counts = dict(
            db.execute(
                select(TenderEvent.delivery, func.count())
                .where(TenderEvent.tender_id == tender.id)
                .group_by(TenderEvent.delivery)
            ).all()
        )
        result.append(
            MonitoredTender(
                tender_id=tender.id,
                external_id=tender.external_id,
                title=tender.title,
                last_checked_at=monitor.last_checked_at,
                events_total=sum(counts.values()),
                events_pending=counts.get(EventDelivery.PENDING.value, 0),
                events_held=counts.get(EventDelivery.HELD.value, 0),
                events_error=counts.get(EventDelivery.ERROR.value, 0),
            )
        )
    return result


@router.get("/monitoring/unseen", response_model=dict[str, int])
def unseen_events(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict[str, int]:
    """Закупки с событиями, которых пользователь ещё не видел: id → число (зелёная точка в
    списке тендеров, раздел 100)."""

    return {str(k): v for k, v in tender_monitor_service.unseen_counts(db, user).items()}


@router.post("/tenders/{tender_id}/monitoring/seen", status_code=status.HTTP_204_NO_CONTENT)
def mark_events_seen(
    tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> None:
    """Пользователь открыл журнал событий — точки по этой закупке гаснут (только у него)."""

    tender_monitor_service.mark_seen(db, user, _tender(db, tender_id).id)


@router.get("/tenders/{tender_id}/monitoring", response_model=MonitoringState)
def get_monitoring(
    tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MonitoringState:
    return _state(db, _tender(db, tender_id), user)


@router.put("/tenders/{tender_id}/monitoring", response_model=MonitoringState)
def set_monitoring(
    tender_id: uuid.UUID,
    body: MonitoringToggle,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MonitoringState:
    """Включение снимает исходный слепок (несколько обращений к площадке и ЕИС)."""

    tender = _tender(db, tender_id)
    if body.enabled:
        tender_monitor_service.enable(db, tender, actor=user)
    else:
        tender_monitor_service.disable(db, tender, actor=user)
    return _state(db, tender, user)


@router.post("/tenders/{tender_id}/monitoring/check", response_model=MonitoringState)
def check_monitoring(
    tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> MonitoringState:
    """Проверка сейчас, не дожидаясь планового прохода, и отправка накопленных событий."""

    tender = _tender(db, tender_id)
    if tender_monitor_service.get_monitor(db, tender.id) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Закупка не поставлена под наблюдение")
    tender_monitor_service.check_tender(db, tender)
    result = tender_event_service.send_pending(db, tender, actor_id=user.id)
    return _state(db, tender, user, result)


@router.post("/tenders/{tender_id}/monitoring/send", response_model=MonitoringState)
def send_events(
    tender_id: uuid.UUID,
    retry_errors: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MonitoringState:
    """Отправка накопленных событий; `retry_errors` возвращает в очередь отклонённые порталом."""

    tender = _tender(db, tender_id)
    if retry_errors:
        tender_event_service.retry_errors(db, tender)
    result = tender_event_service.send_pending(db, tender, actor_id=user.id)
    return _state(db, tender, user, result)
