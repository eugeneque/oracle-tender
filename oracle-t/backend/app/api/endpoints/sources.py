import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.source import Source
from app.models.user import User
from app.schemas.source import SourceOut, SourcePollResultOut, SourcesPollRequest
from app.services.tender_service import list_sources, poll_source, poll_sources

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
def get_sources(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[Source]:
    return list_sources(db)


@router.post("/poll", response_model=list[SourcePollResultOut])
def post_poll_sources(
    payload: SourcesPollRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Опрос выбранного набора источников — кнопка «Синхронизировать» / модалка «Ресурсы»
    на странице тендеров. Доступно любому авторизованному пользователю (в отличие от
    поточечного `/sources/{id}/poll` в «Настройках», который остаётся действием администратора
    по разделу 5.6 ТЗ) — это рабочий сценарий "обновить список тендеров", а не управление
    источниками."""

    return poll_sources(db, payload.source_keys, actor_id=user.id)


@router.post("/{source_id}/poll", response_model=SourcePollResultOut)
def post_poll_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Источник не найден")
    return poll_source(db, source, actor_id=admin.id)
