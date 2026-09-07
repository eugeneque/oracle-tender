import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.integration import ApiClientCreate, ApiClientCreated, ApiClientOut
from app.schemas.integration_setting import (
    YandexAiStudioSettingsOut,
    YandexAiStudioSettingsUpdate,
    YandexConnectionTestResult,
)
from app.services import api_client_service, yandex_ai_service

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/yandex-ai-studio", response_model=YandexAiStudioSettingsOut)
def get_yandex_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> YandexAiStudioSettingsOut:
    return yandex_ai_service.get_settings_out(db)


@router.patch("/yandex-ai-studio", response_model=YandexAiStudioSettingsOut)
def update_yandex_settings(
    payload: YandexAiStudioSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> YandexAiStudioSettingsOut:
    return yandex_ai_service.update_settings(db, payload, actor=admin)


@router.post("/yandex-ai-studio/test", response_model=YandexConnectionTestResult)
def test_yandex_connection(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> YandexConnectionTestResult:
    return yandex_ai_service.test_connection(db, actor=admin)


@router.get("/api-clients", response_model=list[ApiClientOut])
def list_api_clients(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> list[ApiClientOut]:
    """Ключи доступа внешних систем (раздел 5.10 ТЗ)."""

    return [
        ApiClientOut.model_validate(client, from_attributes=True)
        for client in api_client_service.list_clients(db)
    ]


@router.post("/api-clients", response_model=ApiClientCreated)
def create_api_client(
    payload: ApiClientCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ApiClientCreated:
    """Выпускает ключ. Значение возвращается **единственный раз** — в базе лежит только хеш,
    и восстановить ключ потом невозможно, только выпустить новый."""

    client, key = api_client_service.create_client(db, name=payload.name, actor=admin)
    return ApiClientCreated(
        **ApiClientOut.model_validate(client, from_attributes=True).model_dump(), key=key
    )


@router.delete("/api-clients/{client_id}", response_model=ApiClientOut)
def revoke_api_client(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ApiClientOut:
    """Отзывает ключ. Запись остаётся: по ней видно, что ключ существовал и когда работал."""

    client = api_client_service.revoke_client(db, client_id, actor=admin)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ключ не найден")
    return ApiClientOut.model_validate(client, from_attributes=True)
