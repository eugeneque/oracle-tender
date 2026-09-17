import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ManufacturerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    brand_name: str | None
    website: str | None
    is_mirtek: bool
    created_at: datetime


class SiTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    manufacturer_id: uuid.UUID
    si_code: str
    notation: str | None = None
    type_name: str | None = None
    description_type_url: str | None
    description_type_version: str | None = None
    has_description_type_text: bool
    has_allowed_modifications: bool = False
    # Исполнения, представленные на испытания, из карточки Аршина; редакция документа, из
    # которой характеристики уже разнесены по моделям; дата, когда ревалидация заметила
    # новую редакцию (замечание заказчика 15.09.2026 — учитывать изменения описания типа).
    tested_modifications: list[str] = []
    description_type_extracted_version: str | None = None
    description_type_changed_at: datetime | None = None
    mpi_months: int | None = None
    valid_to: date | None = None
    is_actual: bool | None = None
    # `brand` — запись сопоставлена с производителем только по торговому имени: под тем же
    # брендом может работать чужое юрлицо, поэтому в интерфейсе такие строки нужно показывать
    # отдельной пометкой при проверке человеком (раздел 5.3 ТЗ).
    matched_by: str | None = None
    source: str
    verified_by_user: bool
    # «Требует ручной проверки»: реестр вернул несколько кандидатов, кандидат оказался не
    # того вида измерений, либо истекает свидетельство об утверждении типа. Отличается от
    # `verified_by_user` («человек подтвердил») — здесь «системе не хватило оснований».
    review_status: str = "ok"
    review_reason: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SiTypeUpdate(BaseModel):
    """Ручная правка/подтверждение записи (раздел 5.3 ТЗ — код СИ обычное редактируемое
    поле). Поле, не присланное в запросе, не меняется (та же PATCH-семантика, что и в
    `YandexAiStudioSettingsUpdate`)."""

    si_code: str | None = None
    verified_by_user: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    manufacturer_id: uuid.UUID
    si_type_id: uuid.UUID | None
    model_name: str
    # Обозначение модели отдельно от наименования: наименование — как у производителя
    # («Счётчик электрической энергии однофазный интеллектуальный НАРТИС-Р1-М»), код —
    # то, по чему прибор сопоставляется с Госреестром («НАРТИС-Р1-М»).
    model_code: str | None = None
    article: str | None
    device_type: str | None
    # Заводское исполнение («Таганрог», «Владивосток»): у одной модели исполнения
    # различаются сроком службы и комплектом документов, и в списке их надо различать.
    execution: str | None = None
    # Полное условное обозначение исполнения из реестра ФГИС — у записей, заведённых из
    # Аршина, а не с сайта производителя.
    registry_modification: str | None = None
    status: str
    source_url: str | None = None
    data_source: str | None = None
    # Характеристики с сайта, которым не нашлось поля в Приложении C. Показываются в
    # карточке отдельным блоком: это данные, а не мусор, просто справочник до них ещё
    # не дорос.
    extra_specifications: dict = {}
    review_status: str = "ok"
    review_reason: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProductCreate(BaseModel):
    model_name: str = Field(min_length=1, max_length=255)
    si_type_id: uuid.UUID | None = None
    article: str | None = None
    device_type: str | None = None


class ProductUpdate(BaseModel):
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    si_type_id: uuid.UUID | None = None
    article: str | None = None
    device_type: str | None = None
    status: str | None = None


class CharacteristicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    group_name: str
    field_name: str
    value: str | None
    source: str
    confidence: float | None
    verified_by_user: bool
    updated_at: datetime


class CharacteristicUpsert(BaseModel):
    """Ручной ввод/правка значения (раздел 5.3 ТЗ, источник 3 — «ручной ввод/импорт через
    админ-панель для любых полей, включая корректировку автоматически извлечённых данных»).
    Сохраняется с `source=manual_entry` и `verified_by_user=true` — такое значение защищено
    от перезаписи повторной AI-экстракцией."""

    group_name: str
    field_name: str
    value: str


class CharacteristicVerify(BaseModel):
    verified_by_user: bool


class ExtractionOutcomeOut(BaseModel):
    saved: int
    skipped_unknown_field: int
    skipped_protected: int
    chunks_processed: int
    chunks_failed: int


class ImportOutcomeOut(BaseModel):
    manufacturers_matched: int
    si_types_created: int
    si_types_updated: int
    products_created: int
    errors: list[str]


class ManualExtractionOutcomeOut(BaseModel):
    """Итог экстракции с сайта производителя: какой документ нашли и что из него извлекли."""

    manual_url: str | None
    manual_title: str | None
    extraction: ExtractionOutcomeOut
    message: str | None


class CatalogTaskOut(BaseModel):
    """Задача очереди пополнения справочника (раздел 5.3 ТЗ; задача 1 задания)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    adapter_key: str
    reason: str
    status: str
    model_name: str | None
    manufacturer_id: uuid.UUID | None
    product_id: uuid.UUID | None
    si_type_id: uuid.UUID | None
    attempts: int
    message: str | None
    # Рассмотренные кандидаты реестра при неоднозначности — чтобы человек видел, из чего
    # выбирала система, не повторяя поиск руками.
    details: dict = {}
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CatalogLookupRequest(BaseModel):
    """Ручная постановка модели в очередь поиска в ФГИС."""

    model_name: str = Field(min_length=2, max_length=255)
    manufacturer_id: uuid.UUID
    product_id: uuid.UUID | None = None


class CatalogSyncOutcomeOut(BaseModel):
    """Итог обхода каталога одного производителя."""

    products_created: int
    products_updated: int
    # Карточки, которые не перезагружались: запись свежая, листинг про неё нового не сообщил.
    products_skipped: int
    characteristics_saved: int
    ai_extracted: int
    si_types_linked: int
    marked_for_review: int
    cards_failed: int
    errors: list[str]


class ManualIngestOutcomeOut(BaseModel):
    """Итог загрузки руководств по эксплуатации.

    Пропуски разделены по причинам: «уже разобрано» — штатный результат повторного запуска,
    «запрещено robots.txt» — сознательное соблюдение чужих правил (у Энергомеры раздел с
    документацией закрыт к обходу), «нет ссылки» — руководства нет на карточке товара."""

    processed: int
    # Модели, получившие уже разобранный документ своего семейства: у 478 моделей справочника
    # всего 134 разных руководства, и повторно платить за разбор одного и того же незачем.
    reused_documents: int
    skipped_have_data: int
    skipped_no_link: int
    skipped_by_robots: int
    failed: int
    characteristics_saved: int
    messages: list[str]


class LinkSiTypesOutcomeOut(BaseModel):
    """Итог привязки моделей к утверждённым типам СИ.

    `not_found` — не ошибка: код СИ для модели может быть просто ещё не найден автопоиском.
    А вот `needs_review` — это модели, где обозначению одинаково соответствуют несколько
    типов; выбирать за человека система не станет."""

    linked: int
    already_linked: int
    needs_review: int
    not_found: int
    details: list[str]


class CatalogSiteOut(BaseModel):
    """Сайт производителя, каталог которого система умеет обходить."""

    adapter_key: str
    manufacturer_legal_name: str
    base_url: str
    categories: int
    # Профиль сайта может быть описан, а строки производителя в справочнике не быть — её
    # заводит администратор. Интерфейс должен показывать это различие, а не прятать источник.
    manufacturer_id: uuid.UUID | None


class CatalogDocumentOut(BaseModel):
    """Строка справочника документов по СИ и руководств с датой актуальности (правка по
    итогам показа 15.09.2026)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    manufacturer_id: uuid.UUID
    product_id: uuid.UUID | None
    si_type_id: uuid.UUID | None
    model_name: str | None
    # Заводское исполнение модели — у МИРТЕК одна модель бывает в двух, с разными документами.
    execution: str | None
    si_code: str | None
    kind: str
    kind_title: str
    source: str
    title: str
    url: str
    document_date: date | None
    document_date_source: str | None
    version_label: str | None
    is_active: bool
    check_status: str
    check_error: str | None
    first_seen_at: datetime
    last_checked_at: datetime | None
    changed_at: datetime | None
    change_count: int


class CatalogDocumentsSummaryOut(BaseModel):
    total: int
    last_checked_at: datetime | None
    # Изменилось при последней сверке — то, что попало в последний отчёт.
    changed_recently: int
    unavailable: int


# --- Обучение справочника по Аршину и поиск документации (замечание заказчика 15.09.2026) ---


class ModificationsOutcomeOut(BaseModel):
    """Итог сверки исполнений из карточек Аршина с каталогом."""

    si_types_scanned: int
    modifications_seen: int
    products_created: int
    products_linked: int
    already_known: int
    created_names: list[str] = []


class DescriptionIngestOutcomeOut(BaseModel):
    """Итог разбора «Описаний типа»: сколько документов вычитано, сколько моделей получили
    характеристики, сколько исполнений расшифровано по условному обозначению."""

    si_types_scanned: int
    documents_fetched: int
    documents_read: int
    products_updated: int
    modifications_decoded: int
    characteristics_saved: int
    skipped_up_to_date: int
    skipped_no_url: int
    failed: int
    messages: list[str] = []


class DiscoveryOutcomeOut(BaseModel):
    """Итог поиска документации на официальном сайте через Яндекс."""

    products_checked: int
    found: int
    not_found: int
    skipped_have_link: int
    queries: int
    messages: list[str] = []


class ProductDocumentationOut(BaseModel):
    """Итог поиска и разбора документации одной модели."""

    manual_url: str | None = None
    reasons: list[str] = []
    ingest: ManualIngestOutcomeOut | None = None
    message: str


class UnknownFieldOut(BaseModel):
    """Характеристика, которой не нашлось поля в Приложении C, — кандидат на расширение
    справочника: сколько моделей её несут и пример значения."""

    field_name: str
    products: int
    sample: str
