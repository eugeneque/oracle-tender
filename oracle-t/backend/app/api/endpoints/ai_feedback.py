"""Раздел настроек «Ответы специалистов» (28.09.2026): все согласия и несогласия с
заключениями ИИ по всем закупкам — кто, когда, что написал, заключение до и после."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.tender import AiFeedbackOut, AiFeedbackPage
from app.services import ai_feedback_service

router = APIRouter(prefix="/ai-feedback", tags=["ai-feedback"])


@router.get("", response_model=AiFeedbackPage)
def list_feedback(
    kind: str | None = Query(default=None, description="agree или disagree"),
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AiFeedbackPage:
    items, total = ai_feedback_service.list_all(db, kind=kind, limit=limit, offset=offset)
    return AiFeedbackPage(items=[AiFeedbackOut(**item) for item in items], total=total)
