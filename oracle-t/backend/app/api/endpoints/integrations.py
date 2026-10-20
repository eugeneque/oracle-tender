import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.tender import Tender
from app.models.user import User
from app.schemas.integration import ApiClientCreate, ApiClientCreated, ApiClientOut
from app.schemas.integration_setting import (
    AiModelLimitsOut,
    AiModelLimitsUpdate,
    AiProviderEnabledUpdate,
    AiProviderKey,
    AiProviderStatus,
    AiProviderSwitch,
    BitrixAiPreview,
    BitrixAiRequestOut,
    BitrixAiSendRequest,
    BitrixAiSettingsOut,
    BitrixAiSettingsUpdate,
    BitrixTenderState,
    Bitrix24CheckResult,
    Bitrix24DealPreview,
    Bitrix24PushResult,
    Bitrix24SettingsOut,
    Bitrix24SettingsUpdate,
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
    bitrix_ai_service,
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


@router.get("/ai-limits", response_model=list[AiModelLimitsOut])
def get_ai_model_limits(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[dict]:
    """Окно контекста и потолок ответа всех моделей (14.10.2026)."""

    return ai_provider_service.limits_overview(db)


@router.put("/ai-limits/{provider}", response_model=list[AiModelLimitsOut])
def update_ai_model_limits(
    provider: AiProviderKey,
    payload: AiModelLimitsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> list[dict]:
    """Задаёт окно контекста и потолок ответа модели; пустые поля — по умолчанию."""

    try:
        return ai_provider_service.update_model_limits(
            db,
            provider,
            context_tokens=payload.context_tokens,
            max_output_tokens=payload.max_output_tokens,
            actor=admin,
        )
    except ValueError as exc:
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


@router.get("/bitrix24/ai", response_model=BitrixAiSettingsOut)
def get_bitrix24_ai_settings(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> BitrixAiSettingsOut:
    """Обработчик приложения «ИИ анализ и решения» на портале (20.10.2026): адрес receive.php,
    признак секрета HMAC и выключатель отправки пакетов."""

    return BitrixAiSettingsOut(**bitrix_ai_service.settings_out(db))


@router.patch("/bitrix24/ai", response_model=BitrixAiSettingsOut)
def update_bitrix24_ai_settings(
    payload: BitrixAiSettingsUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> BitrixAiSettingsOut:
    changes = payload.model_dump(include=payload.model_fields_set)
    try:
        return BitrixAiSettingsOut(**bitrix_ai_service.update_settings(db, changes, actor=admin))
    except bitrix_ai_service.BitrixAiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/bitrix24/ai/preview", response_model=BitrixAiPreview)
def preview_bitrix24_ai_package(
    tender: str,
    full_snapshot: bool = False,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> BitrixAiPreview:
    """Пакет, который уйдёт на портал, — без отправки и без подписи."""

    try:
        found = bitrix_ai_service.resolve_tender(db, tender)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return BitrixAiPreview(**bitrix_ai_service.preview(db, found, full_snapshot=full_snapshot))


@router.post("/bitrix24/ai/send", response_model=BitrixAiRequestOut)
def send_bitrix24_ai_package(
    payload: BitrixAiSendRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> BitrixAiRequestOut:
    """Подписывает и отправляет пакет. Ответ портала (в том числе отказ) возвращается записью
    журнала: HTTP-код и тело ответа нужны администратору Bitrix24 для разбора."""

    try:
        found = bitrix_ai_service.resolve_tender(db, payload.tender)
        return BitrixAiRequestOut(
            **bitrix_ai_service.send(db, found, actor=admin, full_snapshot=payload.full_snapshot)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except bitrix_ai_service.BitrixAiPushDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except bitrix_ai_service.BitrixAiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/bitrix24/ai/requests/{request_pk}/retry", response_model=BitrixAiRequestOut)
def retry_bitrix24_ai_package(
    request_pk: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> BitrixAiRequestOut:
    """Повтор технически не принятого пакета тем же request_id и теми же байтами."""

    try:
        return BitrixAiRequestOut(**bitrix_ai_service.retry(db, request_pk, actor=admin))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except bitrix_ai_service.BitrixAiPushDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except bitrix_ai_service.BitrixAiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/bitrix24/tenders/{tender_id}", response_model=BitrixTenderState)
def get_bitrix24_tender_state(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> BitrixTenderState:
    """Состояние кнопки «Загрузить в Битрикс»: можно ли выгрузить и куда уже выгружено."""

    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return BitrixTenderState(**bitrix_ai_service.tender_state(db, tender))


@router.post("/bitrix24/tenders/{tender_id}", response_model=BitrixTenderState)
def upload_bitrix24_tender(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BitrixTenderState:
    """Выгрузка проверенной закупки с анализом в сделку Bitrix24 (20.10.2026). Доступна
    любому пользователю: тендер в CRM передаёт тот, кто его проверил. Отказ портала не
    ошибка эндпоинта — он виден в `last_request` с кодом и текстом ответа."""

    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    try:
        return BitrixTenderState(**bitrix_ai_service.upload_from_card(db, tender, actor=user))
    except bitrix_ai_service.BitrixAiPushDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except bitrix_ai_service.BitrixAiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/bitrix24/ai/requests", response_model=list[BitrixAiRequestOut])
def list_bitrix24_ai_requests(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> list[BitrixAiRequestOut]:
    return [BitrixAiRequestOut(**item) for item in bitrix_ai_service.list_requests(db)]


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
