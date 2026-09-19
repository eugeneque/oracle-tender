import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.integration import ApiClientCreate, ApiClientCreated, ApiClientOut
from app.schemas.integration_setting import (
    AiProviderStatus,
    AiProviderSwitch,
    RouterAiSettingsOut,
    RouterAiSettingsUpdate,
    RusprofileSettingsOut,
    RusprofileSettingsUpdate,
    YandexAiStudioSettingsOut,
    YandexAiStudioSettingsUpdate,
    YandexConnectionTestResult,
)
from app.services import (
    ai_provider_service,
    api_client_service,
    rusprofile_service,
    yandex_ai_service,
)
from app.services.ai_provider_service import AiNotConfiguredError

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


@router.get("/ai-provider", response_model=AiProviderStatus)
def get_ai_provider(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> AiProviderStatus:
    """Модель, обслуживающая запросы текущего пользователя (личный выбор или системная по
    умолчанию). Для любого пользователя: карточка тендера подписывает и красит блок
    «Разбор ИИ» под неё. Ключей в ответе нет."""

    return ai_provider_service.get_status(db, user)


@router.put("/ai-provider", response_model=AiProviderStatus)
def switch_default_ai_provider(
    payload: AiProviderSwitch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AiProviderStatus:
    """Меняет системную модель по умолчанию — для задач по расписанию и пользователей без
    собственного выбора (свой выбор — `PUT /auth/me/ai-provider`). На ненастроенную
    переключиться нельзя — 400 с подсказкой, что заполнить."""

    try:
        return ai_provider_service.switch_default_provider(
            db, payload.active_provider, actor=admin
        )
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/routerai", response_model=RouterAiSettingsOut)
def get_routerai_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> RouterAiSettingsOut:
    return ai_provider_service.get_routerai_settings_out(db)


@router.patch("/routerai", response_model=RouterAiSettingsOut)
def update_routerai_settings(
    payload: RouterAiSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> RouterAiSettingsOut:
    return ai_provider_service.update_routerai_settings(db, payload, actor=admin)


@router.post("/routerai/test", response_model=YandexConnectionTestResult)
def test_routerai_connection(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> YandexConnectionTestResult:
    return ai_provider_service.test_routerai_connection(db, actor=admin)


@router.get("/rusprofile", response_model=RusprofileSettingsOut)
def get_rusprofile_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> RusprofileSettingsOut:
    """Учётная запись rusprofile.ru (18.09.2026): под ней раздел «Моя компания» заполняется с
    сайта — реквизиты, лицензии, реализованные проекты и история участий с проигрышами."""

    return rusprofile_service.get_settings_out(db)


@router.patch("/rusprofile", response_model=RusprofileSettingsOut)
def update_rusprofile_settings(
    payload: RusprofileSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> RusprofileSettingsOut:
    return rusprofile_service.update_settings(db, payload, actor=admin)


@router.post("/rusprofile/test", response_model=YandexConnectionTestResult)
def test_rusprofile_connection(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> YandexConnectionTestResult:
    return rusprofile_service.test_connection(db, actor=admin)


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
