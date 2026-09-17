"""Реестры допуска продукции: ПП 719 (ГИСП), ЗАК ПАО «Россети», реестр российского ПО
(замечание тестировщика 16.09.2026).

Маршруты — у сущностей, к которым относятся: перечень реестров в каталоге, записи у
модели, проверка по требованиям — у закупки."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.manufacturer import Product
from app.models.user import User
from app.schemas.registry_record import (
    ProductRegistryRowOut,
    RegistryInfoOut,
    RegistryRecordOut,
    RegistryRecordUpsert,
    TenderRegistryCheckOut,
)
from app.services import registry_records_service
from app.services.tender_service import get_tender_by_id

router = APIRouter(tags=["registry-records"])


def _get_product_or_404(db: Session, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Модель не найдена")
    return product


def _check_registry(registry: str) -> None:
    if registry not in registry_records_service.REGISTRIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Реестр «{registry}» неизвестен"
        )


@router.get("/catalog/registries", response_model=list[RegistryInfoOut])
def get_registries(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    """Реестры допуска, которые система умеет проверять, с официальными ссылками."""

    return registry_records_service.list_registries(db)


@router.get("/products/{product_id}/registry-records", response_model=list[ProductRegistryRowOut])
def get_product_records(
    product_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    product = _get_product_or_404(db, product_id)
    return registry_records_service.product_overview(db, product)


@router.put(
    "/products/{product_id}/registry-records/{registry}", response_model=RegistryRecordOut
)
def put_product_record(
    product_id: uuid.UUID,
    registry: str,
    payload: RegistryRecordUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Завести или обновить запись по паре «модель × реестр»."""

    product = _get_product_or_404(db, product_id)
    _check_registry(registry)
    try:
        record = registry_records_service.upsert_record(
            db,
            product,
            registry,
            presence=payload.presence,
            record_number=payload.record_number,
            issued_at=payload.issued_at,
            valid_to=payload.valid_to,
            url=payload.url,
            note=payload.note,
            verified=payload.verified,
            actor=admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return registry_records_service.record_out(record)


@router.delete(
    "/products/{product_id}/registry-records/{registry}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_product_record(
    product_id: uuid.UUID,
    registry: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _get_product_or_404(db, product_id)
    _check_registry(registry)
    record = registry_records_service.get_record(db, product_id, registry)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Записи нет")
    registry_records_service.delete_record(db, record, actor=admin)
    return None


@router.get("/tenders/{tender_id}/registry-check", response_model=TenderRegistryCheckOut)
def get_tender_registry_check(
    tender_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    """Требования закупки о допуске и состояние записей у каждого производителя."""

    tender = get_tender_by_id(db, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return registry_records_service.tender_overview(db, tender)
