"""Реестры допуска продукции: ПП 719 (ГИСП), ЗАК ПАО «Россети», реестр российского ПО
(замечание тестировщика 16.09.2026).

Маршруты — у сущностей, к которым относятся: перечень реестров в каталоге, записи у
модели, проверка по требованиям — у закупки."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.manufacturer import Product
from app.models.user import User
from app.schemas.registry_record import (
    ProductRegistryRowOut,
    RegistryInfoOut,
    RegistryPollOut,
    RegistryPollStatusOut,
    RegistryRecordOut,
    RegistryRecordUpsert,
    TenderRegistryCheckOut,
)
from app.services import registry_polling, registry_records_service
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


# Выгрузка ГИСП целиком — десятки мегабайт.
MAX_REGISTRY_UPLOAD_BYTES = 300 * 1024 * 1024


def _check_pollable(registry: str) -> None:
    _check_registry(registry)
    if registry not in registry_polling.POLLABLE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Этот реестр автоматически не сверяется — записи заводятся в карточке модели",
        )


@router.get("/catalog/registries/polls", response_model=list[RegistryPollStatusOut])
def get_registry_polls(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    """Когда и чем закончилась автоматическая сверка по каждому реестру (13.10.2026)."""

    polls = registry_polling.latest_polls(db)
    infos = {item["key"]: item for item in registry_records_service.list_registries(db)}
    return [
        {
            "registry": registry,
            "label": infos[registry]["label"],
            "url": infos[registry]["url"],
            "last_poll": registry_records_service.poll_out(polls[registry]["last"]),
            "last_successful_poll": registry_records_service.poll_out(polls[registry]["last_ok"]),
        }
        for registry in registry_polling.POLLABLE
    ]


@router.post(
    "/catalog/registries/{registry}/poll",
    response_model=RegistryPollOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_registry_poll(registry: str, admin: User = Depends(require_admin)):
    """Скачать выгрузку реестра и разложить по моделям. Идёт в фоне; ход — в
    `GET /catalog/registries/polls`."""

    _check_pollable(registry)
    return registry_records_service.poll_out(
        registry_polling.poll_in_background(registry, actor_id=admin.id)
    )


@router.post("/catalog/registries/{registry}/upload", response_model=RegistryPollOut)
def post_registry_upload(
    registry: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Выгрузка, скачанная из браузера (XLSX ГИСП или PDF перечня Россетей), — когда реестр
    не отдал её серверу."""

    _check_pollable(registry)
    content = file.file.read(MAX_REGISTRY_UPLOAD_BYTES + 1)
    if len(content) > MAX_REGISTRY_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл больше {MAX_REGISTRY_UPLOAD_BYTES // (1024 * 1024)} МБ",
        )
    poll = registry_polling.poll_uploaded_file(
        db, registry, content=content, file_name=file.filename or "", actor=admin
    )
    if poll.status != "ok":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=poll.message)
    return registry_records_service.poll_out(poll)


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
