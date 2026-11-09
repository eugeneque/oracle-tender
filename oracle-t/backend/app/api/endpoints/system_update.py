"""Страница «Обновление» — проверка и установка новой версии из интерфейса (09.11.2026,
раздел 91 ARCHITECTURE.md). Только администратор: установка перезапускает систему для всех."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.log import LogLevel
from app.models.user import User
from app.services import system_update_service as svc
from app.services.audit import log_action

router = APIRouter(prefix="/system/update", tags=["system-update"])


class StartUpdateIn(BaseModel):
    force: bool = False


def _fail(exc: svc.UpdateError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/status")
def get_status(_user: User = Depends(require_admin)) -> dict:
    return svc.get_status()


@router.post("/check")
def check(_user: User = Depends(require_admin)) -> dict:
    try:
        return svc.check()
    except svc.UpdateError as exc:
        raise _fail(exc) from exc


@router.get("/run")
def get_run(_user: User = Depends(require_admin)) -> dict:
    return svc.run_info()


@router.post("/start")
def start(
    payload: StartUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    try:
        info = svc.request_update(username=user.username, force=payload.force)
    except svc.UpdateError as exc:
        raise _fail(exc) from exc
    log_action(
        db,
        component="system_update",
        action="Обновление системы",
        result="запущено из интерфейса" + (" (развернуть заново)" if payload.force else ""),
        level=LogLevel.WARNING,
        user_id=user.id,
    )
    db.commit()
    return info


@router.post("/cancel")
def cancel(db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict:
    try:
        info = svc.cancel_request()
    except svc.UpdateError as exc:
        raise _fail(exc) from exc
    log_action(db, component="system_update", action="Обновление системы", result="запрос отменён", user_id=user.id)
    db.commit()
    return info
