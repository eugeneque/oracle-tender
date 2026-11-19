"""Банковские гарантии и сведения реестров ЕИС по закупке (19.11.2026, раздел 115)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tender import Tender
from app.models.user import User
from app.services import eis_registry_service, guarantee_service

router = APIRouter(tags=["tender-registry"])


class GuaranteeIn(BaseModel):
    kind: str = Field(pattern="^(APPLICATION|CONTRACT|WARRANTY)$")
    number: str | None = Field(default=None, max_length=100)
    bank: str | None = Field(default=None, max_length=300)
    amount: Decimal | None = Field(default=None, ge=0)
    valid_until: date
    note: str | None = None


class GuaranteeOut(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    number: str | None
    bank: str | None
    amount: Decimal | None
    valid_until: date
    note: str | None
    covered: bool | None
    needed_until: date | None
    basis: str


class RegistryOut(BaseModel):
    complaints: list[dict[str, Any]]
    contracts: list[dict[str, Any]]
    customer_history: list[dict[str, Any]]
    registry_checked_at: datetime | None


def _tender(db: Session, tender_id: uuid.UUID) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return tender


def _guarantees(db: Session, tender: Tender) -> list[GuaranteeOut]:
    return [
        GuaranteeOut(
            id=row["guarantee"].id,
            kind=row["guarantee"].kind,
            kind_label=guarantee_service.KINDS[row["guarantee"].kind],
            number=row["guarantee"].number,
            bank=row["guarantee"].bank,
            amount=row["guarantee"].amount,
            valid_until=row["guarantee"].valid_until,
            note=row["guarantee"].note,
            covered=row["covered"],
            needed_until=row["needed_until"],
            basis=row["basis"],
        )
        for row in guarantee_service.check(db, tender)
    ]


@router.get("/tenders/{tender_id}/guarantees", response_model=list[GuaranteeOut])
def list_guarantees(tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _guarantees(db, _tender(db, tender_id))


@router.post("/tenders/{tender_id}/guarantees", response_model=list[GuaranteeOut], status_code=status.HTTP_201_CREATED)
def add_guarantee(
    tender_id: uuid.UUID, body: GuaranteeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    tender = _tender(db, tender_id)
    try:
        guarantee_service.create(db, tender, body.model_dump(), actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _guarantees(db, tender)


@router.delete("/tenders/{tender_id}/guarantees/{guarantee_id}", response_model=list[GuaranteeOut])
def delete_guarantee(
    tender_id: uuid.UUID, guarantee_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    tender = _tender(db, tender_id)
    try:
        guarantee_service.delete(db, tender, guarantee_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _guarantees(db, tender)


@router.get("/tenders/{tender_id}/eis-registry", response_model=RegistryOut)
def get_registry(tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return RegistryOut(**eis_registry_service.get(db, _tender(db, tender_id)))


@router.post("/tenders/{tender_id}/eis-registry/refresh", response_model=RegistryOut)
def refresh_registry(tender_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Перечитать реестры жалоб и договоров ЕИС и историю заказчика (десятки обращений к ЕИС)."""

    return RegistryOut(**eis_registry_service.refresh(db, _tender(db, tender_id), force=True))
