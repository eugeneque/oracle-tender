from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.activity import ActivityFeed
from app.services import activity_service

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=ActivityFeed)
def get_activity(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ActivityFeed:
    """Центр уведомлений в шапке (14.10.2026): запуски пользователя с итогами за сутки и
    идущие общие процессы со шкалой хода. Интерфейс опрашивает его каждые несколько секунд,
    пока что-то идёт."""

    return activity_service.feed(db, user)
