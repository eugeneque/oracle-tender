"""Интеграции МИРТЕК в ПО верхнего уровня — данные специалиста (05.11.2026).

Читать и править могут администратор и тендерный специалист; аналитику «Каталог продукции»
закрыт на уровне роутера (`deny_analyst_catalog`). Правки попадают в черновик, в анализ
тендеров — только после `POST /software-integrations/publish` (кнопка «Сохранить»)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.adapters.upper_software import PLATFORMS
from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.manufacturer import Product
from app.models.software_integration import SoftwareIntegration, SoftwareIntegrationDocument
from app.models.user import User
from app.schemas.software_integration import (
    DocumentOut,
    IntegrationCreateIn,
    IntegrationOut,
    IntegrationsOverviewOut,
    IntegrationUpdateIn,
    ProductIntegrationOut,
    PublicationOut,
    PublishIn,
    PublishOutcomeOut,
)
from app.services import software_integration_scan
from app.services import software_integration_service as service
from app.services.audit import log_action

router = APIRouter(tags=["software-integrations"])
COMPONENT = "software_integrations"


def _get_or_404(db: Session, integration_id: uuid.UUID) -> SoftwareIntegration:
    item = service.get_integration(db, integration_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ПО не найдено")
    return item


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _publication_out(db: Session) -> list[PublicationOut]:
    return [
        PublicationOut(
            id=record.id, published_at=record.published_at, published_by=name,
            comment=record.comment, changes=record.changes or [],
        )
        for record, name in service.list_publications(db)
    ]


@router.get("/software-integrations", response_model=IntegrationsOverviewOut)
def get_overview(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return {
        "items": [service.to_out(item) for item in service.list_integrations(db)],
        "fields": [
            {"key": key, "label": label, "with_verdict": with_verdict}
            for key, (label, with_verdict) in service.FIELDS.items()
        ],
        "publications": _publication_out(db),
        "platforms": [{"key": key, "name": profile.name} for key, profile in PLATFORMS.items()],
    }


@router.post("/software-integrations", response_model=IntegrationOut, status_code=status.HTTP_201_CREATED)
def post_integration(
    payload: IntegrationCreateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    try:
        item = service.create_integration(
            db, name=payload.name, aliases=payload.aliases, platform_key=payload.platform_key, actor=user
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    log_action(db, component=COMPONENT, action="create", result="success", details=f"Добавлено ПО «{item.name}»", user_id=user.id)
    db.commit()
    return service.to_out(item)


@router.put("/software-integrations/{integration_id}", response_model=IntegrationOut)
def put_integration(
    integration_id: uuid.UUID,
    payload: IntegrationUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Сохраняет правки в черновик. В анализ тендеров они не попадают до «Сохранить»."""

    item = _get_or_404(db, integration_id)
    try:
        service.update_integration(
            db, item, payload.model_dump(exclude_unset=True), actor=user
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    db.commit()
    return service.to_out(item)


@router.post("/software-integrations/{integration_id}/reset", response_model=IntegrationOut)
def post_reset(
    integration_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    item = _get_or_404(db, integration_id)
    service.reset_draft(db, item)
    log_action(db, component=COMPONENT, action="reset", result="success", details=f"Отменены правки «{item.name}»", user_id=user.id)
    db.commit()
    return service.to_out(item)


@router.delete("/software-integrations/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_integration(
    integration_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    item = _get_or_404(db, integration_id)
    name = item.name
    service.delete_integration(db, item)
    log_action(db, component=COMPONENT, action="delete", result="success", details=f"Удалено ПО «{name}»", user_id=user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/software-integrations/publish", response_model=PublishOutcomeOut)
def post_publish(
    payload: PublishIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Общая кнопка «Сохранить»: проверенное из черновиков уходит в анализ тендеров."""

    record = service.publish(db, actor=user, comment=payload.comment if payload else None)
    if record is None:
        return PublishOutcomeOut(published=False, changed=0, message="Изменений для сохранения нет")
    log_action(
        db, component=COMPONENT, action="publish", result="success",
        details=f"Опубликованы данные по ПО верхнего уровня: изменено записей — {len(record.changes)}",
        user_id=user.id,
    )
    db.commit()
    return PublishOutcomeOut(
        published=True, changed=len(record.changes),
        message=f"Сохранено: изменено записей — {len(record.changes)}. Данные используются в анализе тендеров.",
    )


@router.post("/software-integrations/autofill", response_model=PublishOutcomeOut)
def post_autofill(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Предложения по публичным спискам в пустые поля черновика (все — «не проверено»)."""

    outcome = service.autofill_from_public_lists(db)
    log_action(
        db, component=COMPONENT, action="autofill", result="success", details=outcome["message"], user_id=user.id
    )
    db.commit()
    return PublishOutcomeOut(published=False, changed=outcome["filled"], message=outcome["message"])


@router.get("/software-integrations/publications", response_model=list[PublicationOut])
def get_publications(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return _publication_out(db)


# ------------------------------------------------------------------------------ сканы


@router.get("/software-integrations/{integration_id}/documents", response_model=list[DocumentOut])
def get_documents(
    integration_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    _get_or_404(db, integration_id)
    return service.list_documents(db, integration_id)


@router.post(
    "/software-integrations/{integration_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
def post_document(
    integration_id: uuid.UUID,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = _get_or_404(db, integration_id)
    content = file.file.read(service.MAX_DOCUMENT_BYTES + 1)
    try:
        document = service.add_document(
            db, item, file_name=file.filename or "scan", content_type=file.content_type,
            content=content, actor=user,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    log_action(
        db, component=COMPONENT, action="document_upload", result="success",
        details=f"Загружен скан «{document.file_name}» к ПО «{item.name}»", user_id=user.id,
    )
    document.analysis_status = "pending"
    db.commit()
    # Разбор — OCR и обращение к модели, десятки секунд: отдаём ответ сразу, интерфейс
    # опрашивает состояние документа.
    background.add_task(software_integration_scan.analyze_in_background, document.id)
    return document


@router.post("/software-integrations/documents/{document_id}/analyze", response_model=DocumentOut)
def post_analyze(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Повторный разбор скана — после смены модели или улучшения распознавания."""

    document = _document_or_404(db, document_id)
    document.analysis_status = "pending"
    db.commit()
    background.add_task(software_integration_scan.analyze_in_background, document.id)
    return document


def _document_or_404(db: Session, document_id: uuid.UUID) -> SoftwareIntegrationDocument:
    document = db.get(SoftwareIntegrationDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    return document


@router.get("/software-integrations/documents/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    document = _document_or_404(db, document_id)
    return Response(
        content=document.content,
        media_type=document.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{_quote(document.file_name)}"},
    )


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name)


@router.delete("/software-integrations/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    document = _document_or_404(db, document_id)
    db.delete(document)
    log_action(
        db, component=COMPONENT, action="document_delete", result="success",
        details=f"Удалён скан «{document.file_name}»", user_id=user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------- каталог


@router.get("/products/{product_id}/software-integrations", response_model=list[ProductIntegrationOut])
def get_product_integrations(
    product_id: uuid.UUID, db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    """В каких ПО верхнего уровня модель МИРТЕК интегрирована — по данным специалиста."""

    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Модель не найдена")
    return service.product_integrations(db, product)
