import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.integration import ApiClientCreate, ApiClientCreated, ApiClientOut
from app.schemas.integration_setting import (
    AiProviderEnabledUpdate,
    AiProviderKey,
    AiProviderStatus,
    AiProviderSwitch,
    Bitrix24CheckResult,
    Bitrix24DealPreview,
    Bitrix24PushResult,
    Bitrix24SettingsOut,
    Bitrix24SettingsUpdate,
    GigaChatSettingsOut,
    GigaChatSettingsUpdate,
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
    bitrix_deal_service,
    rusprofile_service,
    yandex_ai_service,
)
from app.services.ai_provider_service import AiNotConfiguredError
from app.services.bitrix_client import BitrixError, BitrixWebhookFormatError
from app.services.bitrix_deal_service import BitrixNotConfiguredError, BitrixPushDisabledError

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


@router.put("/ai-provider/{provider}/enabled", response_model=AiProviderStatus)
def set_ai_provider_enabled(
    provider: AiProviderKey,
    payload: AiProviderEnabledUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AiProviderStatus:
    """Включает или выключает модель для всех (29.09.2026). Выключенная модель не получает
    ни одного запроса, а начатые через неё задачи останавливаются."""

    return ai_provider_service.set_provider_enabled(db, provider, payload.enabled, actor=admin)


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


@router.get("/gigachat", response_model=GigaChatSettingsOut)
def get_gigachat_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> GigaChatSettingsOut:
    return ai_provider_service.get_gigachat_settings_out(db)


@router.patch("/gigachat", response_model=GigaChatSettingsOut)
def update_gigachat_settings(
    payload: GigaChatSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> GigaChatSettingsOut:
    try:
        return ai_provider_service.update_gigachat_settings(db, payload, actor=admin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/gigachat/test", response_model=YandexConnectionTestResult)
def test_gigachat_connection(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> YandexConnectionTestResult:
    return ai_provider_service.test_gigachat_connection(db, actor=admin)


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


@router.get("/bitrix24", response_model=Bitrix24SettingsOut)
def get_bitrix24_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> Bitrix24SettingsOut:
    """Подключение к порталу Bitrix24 (08.10.2026): тендеры уходят сделками в выбранную
    воронку и стадию. Отправка по умолчанию выключена — портал боевой."""

    return Bitrix24SettingsOut(**bitrix_deal_service.get_settings_out(db))


@router.patch("/bitrix24", response_model=Bitrix24SettingsOut)
def update_bitrix24_settings(
    payload: Bitrix24SettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> Bitrix24SettingsOut:
    changes = payload.model_dump(include=payload.model_fields_set)
    try:
        return Bitrix24SettingsOut(
            **bitrix_deal_service.update_settings(db, changes, actor=admin)
        )
    except (BitrixWebhookFormatError, BitrixNotConfiguredError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/bitrix24/test", response_model=Bitrix24CheckResult)
def test_bitrix24_connection(
    db: Session = Depends(get_db), admin: User = Depends(require_admin)
) -> Bitrix24CheckResult:
    """Только чтение: стадии воронки и список полей сделки. В CRM ничего не пишется."""

    return Bitrix24CheckResult(**bitrix_deal_service.check_connection(db, actor=admin))


@router.get("/bitrix24/deals/{tender_id}/preview", response_model=Bitrix24DealPreview)
def preview_bitrix24_deal(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Bitrix24DealPreview:
    """Какая сделка получится из тендера. Портал не вызывается."""

    try:
        preview = bitrix_deal_service.preview_deal(db, tender_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Bitrix24DealPreview(**{**preview, "tender_id": str(preview["tender_id"])})


@router.post("/bitrix24/deals/{tender_id}", response_model=Bitrix24PushResult)
def push_bitrix24_deal(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> Bitrix24PushResult:
    """Создаёт или обновляет сделку под тендер. Пока отправка выключена — 409.

    Только администратор: на этапе проверки интеграции сделки в боевую CRM кладёт один
    человек, а не каждый, кто открыл карточку тендера."""

    try:
        return Bitrix24PushResult(**bitrix_deal_service.push_tender(db, tender_id, actor=admin))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BitrixPushDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BitrixNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BitrixError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/bitrix24/test-deal", response_model=Bitrix24PushResult)
def push_bitrix24_test_deal(
    tender_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> Bitrix24PushResult:
    """Одна тестовая сделка «[ТЕСТ] …» из последнего (или указанного) тендера — посмотреть на
    портале, как легли поля. Работает и при выключенной отправке: это явное разовое действие."""

    try:
        return Bitrix24PushResult(
            **bitrix_deal_service.push_test_deal(db, actor=admin, tender_id=tender_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BitrixNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BitrixError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


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
