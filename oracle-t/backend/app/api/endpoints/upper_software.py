"""ПО верхнего уровня: списки поддерживаемого оборудования и интеграция приборов
(замечание тестировщика 16.09.2026).

Маршруты разнесены по трём сущностям, к которым относятся, а не собраны под одним
префиксом: площадки — в каталоге, статус — у производителя и модели, сводка — у закупки."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.manufacturer import Product
from app.models.user import User
from app.schemas.manufacturer import CatalogTaskOut
from app.schemas.upper_software import (
    PlatformSupportOut,
    ProductSupportOut,
    TenderUpperSoftwareOut,
    UpperSoftwarePlatformOut,
    UpperSoftwareSyncOutcomeOut,
)
from app.services import product_catalog_service, upper_software_service
from app.services.tender_service import get_tender_by_id

router = APIRouter(tags=["upper-software"])


def _check_platform(db: Session, adapter_key: str) -> None:
    if upper_software_service.resolve_source(db, adapter_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Площадка «{adapter_key}» неизвестна либо не заведена в источниках",
        )


@router.get("/catalog/upper-software", response_model=list[UpperSoftwarePlatformOut])
def get_platforms(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    """Семь площадок ПО верхнего уровня с состоянием их списков."""

    return upper_software_service.list_platforms(db)


@router.post(
    "/catalog/upper-software/{adapter_key}/sync",
    response_model=CatalogTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_platform_sync(
    adapter_key: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    """Ручное чтение списка одной площадки — в очередь, как обход каталога."""

    _check_platform(db, adapter_key)
    task = upper_software_service.enqueue_sync(db, adapter_key=adapter_key, actor_id=admin.id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Исполнитель площадки «{adapter_key}» не зарегистрирован — обратитесь к администратору",
        )
    return task


@router.post(
    "/catalog/upper-software/{adapter_key}/sync-now",
    response_model=UpperSoftwareSyncOutcomeOut,
)
def post_platform_sync_now(
    adapter_key: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    """Синхронное чтение — для отладки и первичного наполнения: результат виден сразу."""

    _check_platform(db, adapter_key)
    try:
        outcome = upper_software_service.sync_platform(db, adapter_key, actor=admin)
    except Exception as exc:  # noqa: BLE001 - сетевая ошибка чужого сайта — ответ, а не 500
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Список площадки «{adapter_key}» прочитать не удалось: {exc}",
        ) from exc
    return UpperSoftwareSyncOutcomeOut(**vars(outcome))


@router.get(
    "/manufacturers/{manufacturer_id}/upper-software", response_model=list[PlatformSupportOut]
)
def get_manufacturer_support(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    manufacturer = product_catalog_service.get_manufacturer_or_none(db, manufacturer_id)
    if manufacturer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Производитель не найден")
    return [vars(item) for item in upper_software_service.manufacturer_support(db, manufacturer)]


@router.get("/products/{product_id}/upper-software", response_model=list[ProductSupportOut])
def get_product_support(
    product_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Модель не найдена")
    return upper_software_service.product_support(db, product)


@router.get("/tenders/{tender_id}/upper-software", response_model=TenderUpperSoftwareOut)
def get_tender_support(
    tender_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    """Требования закупки об интеграции и матрица «производитель × площадка»."""

    tender = get_tender_by_id(db, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return upper_software_service.tender_overview(db, tender)
