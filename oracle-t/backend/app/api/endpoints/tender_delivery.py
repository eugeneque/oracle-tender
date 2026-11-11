"""Условия поставки (раздел 85), условия договора (раздел 95) и итоги из протоколов (раздел 96)."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tender import Tender
from app.models.user import User
from app.services import contract_conditions_service, delivery_terms_service, protocol_results_service
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


@router.get("/tenders/{tender_id}/contract-conditions")
def get_contract_conditions(
    tender_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> dict[str, Any]:
    return contract_conditions_service.get_conditions(db, _tender(db, tender_id))


@router.post("/tenders/{tender_id}/contract-conditions/extract")
def extract_contract_conditions(
    tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Повторный разбор условий договора по уже скачанной документации (один запрос к модели)."""

    tender = _tender(db, tender_id)
    try:
        contract_conditions_service.extract(db, tender, actor=user)
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return contract_conditions_service.get_conditions(db, tender)


@router.get("/tenders/{tender_id}/protocol-results")
def get_protocol_results(
    tender_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> dict[str, Any]:
    return protocol_results_service.get_results(db, _tender(db, tender_id))


@router.post("/tenders/{tender_id}/protocol-results/extract")
def extract_protocol_results(
    tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Повторный разбор протоколов по уже скачанным файлам (один запрос к модели)."""

    tender = _tender(db, tender_id)
    try:
        found = protocol_results_service.extract(db, tender, actor=user)
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not found["protocols"] and not protocol_results_service.get_results(db, tender)["lots"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="У закупки нет протоколов с распознанным текстом — разбирать нечего.",
        )
    return protocol_results_service.get_results(db, tender)
