from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.notification import TRIGGER_TITLES, Notification
from app.models.user import User
from app.schemas.notification import (
    KnownRecipient,
    NotificationBroadcastRequest,
    NotificationOut,
    NotificationSettingsOut,
    NotificationSettingsUpdate,
    NotificationTestResult,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def get_notifications(
    trigger: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[NotificationOut]:
    """Журнал уведомлений (раздел 5.8 ТЗ) — отдельный от общего журнала блок в настройках,
    доступен всем пользователям: кому и о чём система написала, видно всей команде."""

    query = select(Notification)
    if trigger:
        query = query.where(Notification.trigger == trigger)
    if status:
        query = query.where(Notification.status == status)
    rows = (
        db.execute(
            query.add_columns(User.full_name)
            .outerjoin(User, User.id == Notification.sent_by_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        .tuples()
        .all()
    )
    return [
        NotificationOut(
            id=item.id,
            created_at=item.created_at,
            trigger=item.trigger,
            trigger_title=TRIGGER_TITLES.get(item.trigger, item.trigger),
            status=item.status,
            channel=item.channel,
            recipients=item.recipients,
            subject=item.subject,
            body=item.body,
            body_html=item.body_html,
            error=item.error,
            tender_id=item.tender_id,
            sent_by=sent_by,
        )
        for item, sent_by in rows
    ]


@router.get("/settings", response_model=NotificationSettingsOut)
def get_notification_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> NotificationSettingsOut:
    return notification_service.get_settings_out(db)


@router.patch("/settings", response_model=NotificationSettingsOut)
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> NotificationSettingsOut:
    return notification_service.update_settings(db, payload, actor=admin)


@router.post("/test", response_model=NotificationTestResult)
def send_test_notification(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> NotificationTestResult:
    """Проверочное письмо. Неудача — ожидаемый результат проверки, а не 500-я ошибка:
    отвечаем описанием причины, чтобы админ увидел его прямо в настройках."""

    entry = notification_service.send_test_notification(db, actor=admin)
    return NotificationTestResult(
        success=entry.status == "sent",
        message=(
            f"Письмо отправлено на {entry.recipients}"
            if entry.status == "sent"
            else entry.error or "Не удалось отправить письмо"
        ),
    )


@router.get("/recipients", response_model=list[KnownRecipient])
def get_known_recipients(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> list[KnownRecipient]:
    """Адреса для подсказок в поле получателей: кому система уже писала. Только для
    администратора — форму рассылки видит тоже только он."""

    return notification_service.known_recipients(db)


@router.post("/broadcast", response_model=NotificationTestResult)
def send_broadcast(
    payload: NotificationBroadcastRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> NotificationTestResult:
    """Произвольное письмо администратора.

    Неудачная отправка — не 500-я ошибка, как и у проверочного письма: недоступный почтовый
    сервер это не поломка нашего API, и администратору нужен текст причины в форме. А вот
    негодные адреса — именно ошибка запроса: письмо не ушло никому, и исправлять надо ввод."""

    try:
        entry = notification_service.send_broadcast(
            db,
            subject=payload.subject.strip(),
            body_html=payload.body_html,
            recipients=[item.strip() for item in payload.recipients if item.strip()],
            actor=admin,
        )
    except notification_service.InvalidRecipients as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Не похоже на адреса: {'; '.join(exc.entries)}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return NotificationTestResult(
        success=entry.status == "sent",
        message=(
            f"Письмо отправлено ({len(payload.recipients)} получ.): {entry.recipients}"
            if entry.status == "sent"
            else entry.error or "Не удалось отправить письмо"
        ),
    )
