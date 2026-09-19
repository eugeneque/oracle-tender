import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.manufacturer import CharacteristicSource, Product, ProductCharacteristic, SiType
from app.models.user import User
from app.schemas.manufacturer import (
    CatalogDocumentOut,
    CatalogDocumentsSummaryOut,
    CatalogLookupRequest,
    CatalogTaskOut,
    CharacteristicOut,
    CharacteristicUpsert,
    CharacteristicVerify,
    ExtractionOutcomeOut,
    ImportOutcomeOut,
    LinkSiTypesOutcomeOut,
    ManualIngestOutcomeOut,
    ManualExtractionOutcomeOut,
    ManufacturerCreate,
    ManufacturerOut,
    ManufacturerUpdate,
    CatalogSiteOut,
    CatalogSyncOutcomeOut,
    DescriptionIngestOutcomeOut,
    DiscoveryOutcomeOut,
    ModificationsOutcomeOut,
    ProductDocumentationOut,
    UnknownFieldOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    SiTypeOut,
    SiTypeUpdate,
)
from app.services import (
    catalog_import,
    catalog_learning,
    catalog_queue_service,
    characteristic_extraction,
    document_discovery,
    document_registry_service,
    fgis_catalog_sync,
    fgis_description_ingest,
    product_manual_ingest,
    catalog_site_sync,
    product_catalog_service,
    registry_modifications,
    si_type_linking,
)
from app.services.ai_client import AiNotConfiguredError

router = APIRouter(tags=["manufacturers"])


def _get_manufacturer_or_404(db: Session, manufacturer_id: uuid.UUID):
    manufacturer = product_catalog_service.get_manufacturer_or_none(db, manufacturer_id)
    if manufacturer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Производитель не найден")
    return manufacturer


def _get_si_type_or_404(db: Session, si_type_id: uuid.UUID) -> SiType:
    si_type = db.get(SiType, si_type_id)
    if si_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Код СИ не найден")
    return si_type


@router.get("/manufacturers", response_model=list[ManufacturerOut])
def get_manufacturers(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return product_catalog_service.list_manufacturers(db)


@router.post("/manufacturers", response_model=ManufacturerOut, status_code=status.HTTP_201_CREATED)
def post_manufacturer(
    payload: ManufacturerCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Новый производитель заводится строкой без переработки схемы (раздел 6.3 ТЗ) —
    администратором из интерфейса, а не миграцией."""
    return product_catalog_service.create_manufacturer(
        db,
        legal_name=payload.legal_name,
        brand_name=payload.brand_name,
        website=payload.website,
        market_share_pct=payload.market_share_pct,
        market_share_source=payload.market_share_source,
        actor=admin,
    )


@router.patch("/manufacturers/{manufacturer_id}", response_model=ManufacturerOut)
def patch_manufacturer(
    manufacturer_id: uuid.UUID,
    payload: ManufacturerUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    return product_catalog_service.update_manufacturer(
        db, manufacturer, fields=payload.model_dump(exclude_unset=True), actor=admin
    )


@router.get("/manufacturers/{manufacturer_id}/si-types", response_model=list[SiTypeOut])
def get_si_types(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _get_manufacturer_or_404(db, manufacturer_id)
    return [
        product_catalog_service.si_type_to_out(si_type)
        for si_type in product_catalog_service.list_si_types(db, manufacturer_id)
    ]


@router.post("/manufacturers/{manufacturer_id}/si-types/search", response_model=list[SiTypeOut])
def post_search_si_types(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    si_types = product_catalog_service.search_si_types(db, manufacturer, actor=admin)
    return [product_catalog_service.si_type_to_out(si_type) for si_type in si_types]


@router.patch("/si-types/{si_type_id}", response_model=SiTypeOut)
def patch_si_type(
    si_type_id: uuid.UUID,
    payload: SiTypeUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    si_type = _get_si_type_or_404(db, si_type_id)
    updated = product_catalog_service.update_si_type(
        db, si_type, si_code=payload.si_code, verified_by_user=payload.verified_by_user, actor=admin
    )
    return product_catalog_service.si_type_to_out(updated)


@router.post("/si-types/{si_type_id}/fetch-description-type", response_model=SiTypeOut)
def post_fetch_description_type(
    si_type_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    si_type = _get_si_type_or_404(db, si_type_id)
    updated = product_catalog_service.fetch_description_type(db, si_type, actor=admin)
    return product_catalog_service.si_type_to_out(updated)


# --- Модели приборов и характеристики (раздел 5.3 ТЗ) ---


def _get_product_or_404(db: Session, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Модель не найдена")
    return product


@router.get("/manufacturers/{manufacturer_id}/products", response_model=list[ProductOut])
def get_products(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _get_manufacturer_or_404(db, manufacturer_id)
    return product_catalog_service.list_products_out(db, manufacturer_id)


@router.post(
    "/manufacturers/{manufacturer_id}/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
)
def post_product(
    manufacturer_id: uuid.UUID,
    payload: ProductCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    if payload.si_type_id is not None:
        _get_si_type_or_404(db, payload.si_type_id)
    return product_catalog_service.create_product(
        db,
        manufacturer,
        model_name=payload.model_name,
        si_type_id=payload.si_type_id,
        article=payload.article,
        device_type=payload.device_type,
        actor=admin,
    )


@router.patch("/products/{product_id}", response_model=ProductOut)
def patch_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product = _get_product_or_404(db, product_id)
    fields = payload.model_dump(exclude_unset=True)
    if "si_type_id" in fields and fields["si_type_id"] is not None:
        _get_si_type_or_404(db, fields["si_type_id"])
    return product_catalog_service.update_product(db, product, fields=fields, actor=admin)


@router.get("/products/{product_id}/characteristics", response_model=list[CharacteristicOut])
def get_characteristics(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _get_product_or_404(db, product_id)
    return characteristic_extraction.list_characteristics(db, product_id)


@router.put("/products/{product_id}/characteristics", response_model=CharacteristicOut)
def put_characteristic(
    product_id: uuid.UUID,
    payload: CharacteristicUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    product = _get_product_or_404(db, product_id)
    try:
        return product_catalog_service.upsert_characteristic_manually(
            db,
            product,
            group_name=payload.group_name,
            field_name=payload.field_name,
            value=payload.value,
            actor=admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/characteristics/{characteristic_id}/verify", response_model=CharacteristicOut)
def post_verify_characteristic(
    characteristic_id: uuid.UUID,
    payload: CharacteristicVerify,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    characteristic = db.get(ProductCharacteristic, characteristic_id)
    if characteristic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Характеристика не найдена")
    return product_catalog_service.set_characteristic_verified(
        db, characteristic, verified=payload.verified_by_user, actor=admin
    )


@router.post(
    "/products/{product_id}/extract-characteristics/from-si-type",
    response_model=ExtractionOutcomeOut,
)
def post_extract_from_si_type(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """AI-экстракция характеристик из текста «Описание типа» привязанного кода СИ
    (раздел 5.3 ТЗ, п.2 алгоритма). Текст должен быть предварительно загружен —
    `POST /si-types/{id}/fetch-description-type`."""

    product = _get_product_or_404(db, product_id)
    if product.si_type_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="У модели не указан код СИ — привязать его можно через PATCH /products/{id}",
        )
    si_type = _get_si_type_or_404(db, product.si_type_id)
    if not si_type.description_type_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Текст «Описание типа» ещё не загружен — сначала выполните "
                "POST /si-types/{id}/fetch-description-type"
            ),
        )

    try:
        outcome = characteristic_extraction.extract_characteristics_for_product(
            db,
            product,
            text=si_type.description_type_text,
            document_source="fgis",
            characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
            actor=admin,
        )
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return ExtractionOutcomeOut(**vars(outcome))


@router.post(
    "/products/{product_id}/extract-characteristics/from-manufacturer-site",
    response_model=ManualExtractionOutcomeOut,
)
def post_extract_from_manufacturer_site(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """AI-экстракция характеристик из «Руководства пользователя» на сайте производителя
    (раздел 5.3 ТЗ, п.3 алгоритма). Обход внешнего сайта занимает десятки секунд."""

    product = _get_product_or_404(db, product_id)
    manufacturer = _get_manufacturer_or_404(db, product.manufacturer_id)
    if not manufacturer.website:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"У производителя «{manufacturer.legal_name}» не указан сайт — "
                "заполните его в справочнике производителей"
            ),
        )

    try:
        outcome = characteristic_extraction.extract_characteristics_from_manufacturer_site(
            db, product, website=manufacturer.website, actor=admin
        )
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return ManualExtractionOutcomeOut(
        manual_url=outcome.manual_url,
        manual_title=outcome.manual_title,
        extraction=ExtractionOutcomeOut(**vars(outcome.extraction)),
        message=outcome.message,
    )


MAX_IMPORT_BYTES = 5 * 1024 * 1024  # защитный предел на размер CSV


@router.post("/catalog/import", response_model=ImportOutcomeOut)
def post_import_catalog(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """CSV-импорт «Производитель → код СИ → модель» (раздел 5.3 ТЗ, п.4 алгоритма)."""

    content = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл больше предела {MAX_IMPORT_BYTES // (1024 * 1024)} МБ",
        )

    outcome = catalog_import.import_catalog_csv(db, content=content, actor=admin)
    return ImportOutcomeOut(**vars(outcome))


# --- Пополнение справочника из внешних источников (задача 1 и 2; раздел 5.3 ТЗ) ---
#
# Обход каталога и запросы к ФГИС занимают минуты, поэтому эндпоинты не выполняют работу
# сами, а ставят задачу в общую очередь и сразу возвращают её. Состояние смотрится через
# `GET /catalog/queue` — та же схема, что у фоновых задач анализа тендера.


@router.get("/catalog/queue", response_model=list[CatalogTaskOut])
def get_catalog_queue(
    adapter_key: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Очередь пополнения справочника. Основной рабочий фильтр —
    `status_filter=needs_review`: это список записей, по которым система отказалась выбирать
    сама (коллизия наименований в реестре, истекающее свидетельство) и ждёт человека."""

    return catalog_queue_service.list_tasks(
        db, adapter_key=adapter_key, status=status_filter, limit=min(limit, 200)
    )


@router.post("/catalog/fgis/lookup", response_model=CatalogTaskOut, status_code=status.HTTP_202_ACCEPTED)
def post_fgis_lookup(
    payload: CatalogLookupRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Поставить модель в очередь поиска в реестре ФГИС вне расписания.

    Тот же путь, которым пользуется модуль сопоставления, когда встречает модель без данных
    (п.1.1 задания, триггер 2) — здесь он доступен человеку явной кнопкой."""

    manufacturer = _get_manufacturer_or_404(db, payload.manufacturer_id)
    product = _get_product_or_404(db, payload.product_id) if payload.product_id else None

    task = fgis_catalog_sync.enqueue_missing_model(
        db,
        model_name=payload.model_name,
        manufacturer=manufacturer,
        product=product,
        actor_id=admin.id,
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Исполнитель источника ФГИС не зарегистрирован — обратитесь к администратору",
        )
    return task


@router.get("/catalog/sites", response_model=list[CatalogSiteOut])
def get_catalog_sites(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Сайты производителей, каталоги которых система умеет обходить.

    Отдаётся вместе с признаком «производитель есть в справочнике»: профиль сайта может быть
    описан, а строки производителя не быть (её заводит администратор), и интерфейс должен
    показывать это различие, а не молча прятать источник."""

    return catalog_site_sync.list_sites(db)


@router.post(
    "/catalog/sites/{adapter_key}/sync",
    response_model=CatalogTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_catalog_site_sync(
    adapter_key: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Ручной запуск обхода каталога одного производителя. Плановый — раз в неделю по всем
    сайтам сразу; здесь та же операция по кнопке для конкретного сайта."""

    if catalog_site_sync.resolve_site(db, adapter_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Источник «{adapter_key}» неизвестен либо его производитель отсутствует "
                "в справочнике"
            ),
        )

    task = catalog_site_sync.enqueue_sync(db, adapter_key=adapter_key, actor_id=admin.id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Исполнитель источника «{adapter_key}» не зарегистрирован — обратитесь к администратору",
        )
    return task


@router.post("/catalog/sites/{adapter_key}/sync-now", response_model=CatalogSyncOutcomeOut)
def post_catalog_site_sync_now(
    adapter_key: str,
    use_ai: bool = True,
    full_refresh: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Синхронный обход каталога — для отладки и первичного наполнения справочника.

    Запрос держится всё время обхода (минуты), поэтому в интерфейсе используется постановка
    в очередь выше; этот эндпоинт нужен там, где важно увидеть результат сразу и целиком:
    при первом заполнении и при разборе, почему обход вернул не то.

    `full_refresh=true` перечитывает и недавно обойдённые карточки — нужно после правки
    профиля сайта или словаря синонимов, когда старые записи надо собрать заново."""

    site = catalog_site_sync.resolve_site(db, adapter_key)
    if site is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Источник «{adapter_key}» неизвестен либо его производитель отсутствует "
                "в справочнике"
            ),
        )
    manufacturer, adapter = site
    outcome = catalog_site_sync.sync_catalog(
        db,
        manufacturer=manufacturer,
        adapter=adapter,
        actor=admin,
        use_ai=use_ai,
        full_refresh=full_refresh,
    )
    return CatalogSyncOutcomeOut(**vars(outcome))


@router.post(
    "/manufacturers/{manufacturer_id}/ingest-manuals",
    response_model=ManualIngestOutcomeOut,
)
def post_ingest_manuals(
    manufacturer_id: uuid.UUID,
    limit: int = 20,
    use_ai: bool = True,
    refresh: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Скачать руководства по эксплуатации моделей производителя и извлечь из них
    характеристики (раздел 5.3 ТЗ, п.3).

    Обход сайта сохраняет только ссылку на руководство, а руководство — самый подробный
    источник о приборе: интерфейсы, протоколы, функции. Без этого шага третий источник
    сопоставления (раздел 5.5 ТЗ, п.3) опирается на пустоту.

    Запрос держится всё время загрузки, поэтому `limit` ограничивает один прогон: документ
    весит мегабайты, а его разбор стоит нескольких обращений к модели. Уже разобранные
    руководства пропускаются, `robots.txt` сайта соблюдается.

    `refresh=true` перечитывает и уже разобранные — нужно после расширения справочника
    характеристик: поля, которых в нём не было, при прошлом разборе отбрасывались."""

    manufacturer = db.get(Manufacturer, manufacturer_id)
    if manufacturer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Производитель не найден"
        )

    outcome = product_manual_ingest.ingest_manuals(
        db, manufacturer, actor=admin, limit=limit, use_ai=use_ai, refresh=refresh
    )
    return ManualIngestOutcomeOut(**vars(outcome))


@router.post(
    "/manufacturers/{manufacturer_id}/link-si-types",
    response_model=LinkSiTypesOutcomeOut,
)
def post_link_si_types(
    manufacturer_id: uuid.UUID,
    relink: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Привязать модели производителя к утверждённым типам СИ (`products.si_type_id`).

    Каталог и реестр наполняются независимо, и без этой связи модуль сопоставления не знает,
    какое «Описание типа» относится к какой модели. Обход каталога МИРТЕК делает этот шаг сам;
    здесь он доступен отдельно — например, после автопоиска в ФГИС по уже заведённым моделям.

    `relink=true` пересчитывает и уже привязанные модели: по умолчанию они не трогаются,
    потому что код СИ мог быть выставлен человеком (раздел 5.3 ТЗ — ручной ввод в приоритете).
    """

    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    outcome = si_type_linking.link_products_to_si_types(
        db, manufacturer, actor_id=admin.id, relink=relink
    )
    return LinkSiTypesOutcomeOut(**vars(outcome))


# --- Справочник документов по СИ и руководств (правка по итогам показа 15.09.2026) ---


@router.get("/catalog/documents", response_model=list[CatalogDocumentOut])
def get_catalog_documents(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Документы производителя с актуальными датами: руководства и паспорта моделей,
    «Описания типа», сертификаты и декларации. Погасшие строки (ссылка пропала из
    справочника) отдаются тоже — они в конце списка."""

    _get_manufacturer_or_404(db, manufacturer_id)
    return document_registry_service.list_documents(db, manufacturer_id=manufacturer_id)


@router.get("/catalog/documents/summary", response_model=CatalogDocumentsSummaryOut)
def get_catalog_documents_summary(
    manufacturer_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return document_registry_service.summary(db, manufacturer_id=manufacturer_id)


@router.post(
    "/catalog/documents/check",
    response_model=CatalogTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_catalog_documents_check(
    manufacturer_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Ручной запуск сверки документов с источниками — по одному производителю или по
    всем. Плановая идёт раз в неделю; здесь та же операция по кнопке. В фоне, через
    очередь справочника: сверка — сотни запросов к чужим сайтам с паузами, это минуты."""

    if manufacturer_id is not None:
        _get_manufacturer_or_404(db, manufacturer_id)
    task = document_registry_service.enqueue_check(
        db, manufacturer_id=manufacturer_id, actor_id=admin.id
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Исполнитель сверки документов не зарегистрирован — обратитесь к администратору",
        )
    return task


# --- Обучение справочника по Аршину и поиск документации (замечание заказчика 15.09.2026) ---
#
# Система должна учиться характеристикам приборов всех производителей по Аршину, замечать
# изменения в «Описании типа» и находить документацию на новые исполнения, которых на сайте
# производителя ещё нет (НАРТИС-И100-W115). Полный проход — в фоне через очередь; отдельные
# шаги доступны и синхронно, чтобы администратор видел результат сразу.


@router.post(
    "/manufacturers/{manufacturer_id}/learn",
    response_model=CatalogTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_learn_manufacturer(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Полный проход обучения по производителю в фоне: карточки Аршина → исполнения →
    «Описание типа» → поиск документации → руководства. Итог — в задаче очереди
    (`GET /catalog/queue?adapter_key=catalog_learning`) и в журнале."""

    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    task = catalog_learning.enqueue(db, manufacturer, actor_id=admin.id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Исполнитель обучения справочника не зарегистрирован — обратитесь к администратору",
        )
    return task


@router.post("/catalog/learn", status_code=status.HTTP_202_ACCEPTED)
def post_learn_all(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Проход обучения по всем производителям — то же, что делает планировщик раз в неделю."""

    queued = catalog_learning.enqueue_all(db, actor_id=admin.id)
    catalog_queue_service.process_queue_in_background()
    return {"queued": queued}


@router.post(
    "/manufacturers/{manufacturer_id}/discover-modifications",
    response_model=ModificationsOutcomeOut,
)
def post_discover_modifications(
    manufacturer_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Исполнения из карточек Аршина → каталог. Карточки без списка исполнений
    перечитываются из ФГИС. Быстрый шаг без обращений к модели."""

    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    catalog_learning.refresh_cards(db, manufacturer)
    outcome = registry_modifications.discover_modifications(db, manufacturer, actor_id=admin.id)
    return ModificationsOutcomeOut(**vars(outcome))


@router.post(
    "/manufacturers/{manufacturer_id}/ingest-description-types",
    response_model=DescriptionIngestOutcomeOut,
)
def post_ingest_description_types(
    manufacturer_id: uuid.UUID,
    limit: int = 10,
    refresh: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Загрузить «Описания типа» электросчётчиков производителя и разнести характеристики
    по привязанным моделям. `limit` — документов за проход (каждый — обращения к модели);
    `refresh=true` перечитывает и уже разобранные."""

    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    outcome = fgis_description_ingest.ingest_description_types(
        db, manufacturer, actor_id=admin.id, limit=limit, refresh=refresh
    )
    return DescriptionIngestOutcomeOut(**vars(outcome))


@router.post(
    "/manufacturers/{manufacturer_id}/discover-documents",
    response_model=DiscoveryOutcomeOut,
)
def post_discover_documents(
    manufacturer_id: uuid.UUID,
    limit: int = 20,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Найти через Яндекс руководства на официальном сайте для моделей без ссылки на
    руководство. Только ссылки; разбор — «Разобрать руководства»."""

    manufacturer = _get_manufacturer_or_404(db, manufacturer_id)
    outcome = document_discovery.discover_documents(
        db, manufacturer, actor_id=admin.id, limit=limit
    )
    return DiscoveryOutcomeOut(**vars(outcome))


@router.post("/products/{product_id}/find-documentation", response_model=ProductDocumentationOut)
def post_find_documentation(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Найти документацию одной модели поиском в интернете (официальный сайт) и сразу
    разобрать её. Для прибора, которого нет в каталоге на сайте производителя, это
    единственный автоматический путь к характеристикам помимо «Описания типа»."""

    product = _get_product_or_404(db, product_id)
    manufacturer = _get_manufacturer_or_404(db, product.manufacturer_id)

    discovery = document_discovery.DiscoveryOutcome()
    found = document_discovery.find_manual_for_product(db, product, manufacturer, outcome=discovery)
    if found is None:
        return ProductDocumentationOut(
            message=(
                discovery.messages[0]
                if discovery.messages
                else (
                    f"На официальном сайте документация для «{product.model_code or product.model_name}» "
                    f"поиском не найдена (запросов: {discovery.queries}). Ссылку можно указать вручную."
                )
            )
        )
    document_discovery.save_manual_link(db, product, found.url)
    ingest = product_manual_ingest.ingest_manuals(
        db, manufacturer, actor=admin, limit=1, refresh=True, products=[product]
    )
    return ProductDocumentationOut(
        manual_url=found.url,
        reasons=found.reasons,
        ingest=ManualIngestOutcomeOut(**vars(ingest)),
        message=(
            f"Найдено {found.url} ({', '.join(found.reasons)}); характеристик сохранено "
            f"{ingest.characteristics_saved}"
            + ("; документ закрыт robots.txt сайта" if ingest.skipped_by_robots else "")
            + ("; документ не загрузился" if ingest.failed else "")
        ),
    )


@router.get("/manufacturers/{manufacturer_id}/unknown-fields", response_model=list[UnknownFieldOut])
def get_unknown_fields(
    manufacturer_id: uuid.UUID,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Характеристики вне Приложения C у моделей производителя — кандидаты на расширение
    справочника (новые характеристики приборов появляются в документации раньше, чем в
    справочнике)."""

    _get_manufacturer_or_404(db, manufacturer_id)
    return characteristic_extraction.unknown_fields_summary(db, manufacturer_id, limit=min(limit, 200))
