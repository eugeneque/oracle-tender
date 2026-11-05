"""Условия поставки закупки: модель поставки, места, график, заявки (раздел 85 ARCHITECTURE.md)."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tender import Tender
from app.models.user import User
from app.services import delivery_terms_service
from app.services.ai_provider_service import AiNotConfiguredError

router = APIRouter(tags=["delivery"])


def _tender(db: Session, tender_id: uuid.UUID) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return tender


@router.get("/tenders/{tender_id}/delivery-terms")
def get_delivery_terms(
    tender_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> dict[str, Any]:
    return delivery_terms_service.get_terms(db, _tender(db, tender_id))


@router.post("/tenders/{tender_id}/delivery-terms/extract")
def extract_delivery_terms(
    tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Повторный разбор условий поставки по уже скачанной документации (один запрос к модели)."""

    tender = _tender(db, tender_id)
    try:
        delivery_terms_service.extract(db, tender, actor=user)
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return delivery_terms_service.get_terms(db, tender)
