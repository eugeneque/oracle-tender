import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TenderSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str


class TenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    title: str
    customer_name: str | None
    organizer_name: str | None
    procurement_method: str | None
    status: str | None
    price: Decimal | None
    currency: str
    application_start: datetime | None
    application_end: datetime | None
    publish_date: date | None
    okpd2_code: str | None
    source_url: str | None
    source: TenderSourceOut
    # Поля этапа 5: заполняются ИИ-анализом документации, до него остаются пустыми.
    tender_type: str | None = None
    region_organizer_code: str | None = None
    region_delivery_code: str | None = None
    federal_district_code: int | None = None
    ai_comment: str | None = None
    # Алиас поверх `stage` (раздел 7 ТЗ): в БД колонки нет, но внешние потребители поле
    # читают, и убирать его из ответа нельзя.
    relevance_status: str = "new"
    stage: str = "ai_selected"
    registry_number: str | None = None
    customer_contact_name: str | None = None
    customer_contact_phone: str | None = None
    customer_contact_email: str | None = None
    assignee_id: uuid.UUID | None = None
    assignee_name: str | None = None
    # Итоговая AI-оценка по профилю (раздел 5.5.1 ТЗ) — главная метрика списка с
    # 03.09.2026. Как и процент победителя, не колонка таблицы: подставляется
    # `attach_analysis_fields` одним запросом на выдачу.
    ai_score: Decimal | None = None
    ai_verdict: str | None = None
    # Процент победителя МИРТЕК (этап 6). Не колонка таблицы, а результат подзапроса в
    # `list_tenders` — в списке он нужен на каждой карточке, отдельным запросом на тендер
    # это дало бы N+1.
    win_percentage: Decimal | None = None
    requirements_count: int = 0
    # Решение модели «это правда наша закупка» (раздел 5.4 ТЗ). `None` — не проверяли;
    # это не то же самое, что `false`, и интерфейс показывает их по-разному.
    ai_relevant: bool | None = None
    ai_relevance_reason: str | None = None
    ai_relevance_confidence: int | None = None
    created_at: datetime
    updated_at: datetime


class TenderStatsOut(BaseModel):
    total: int
    by_status: dict[str, int]


class TenderDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str
    file_type: str | None
    parse_status: str
    parse_error: str | None
    downloaded_at: datetime | None
    # Класс документа и звёздочка приоритета (раздел 5.6 ТЗ, вкладка «Документы»).
    document_class: str | None = None
    document_class_label: str | None = None
    is_priority_source: bool = False
    # Разобран ли документ до текста: статуса `success` для этого мало — у неподдержанного
    # формата он тоже `success`, просто текста нет (и в ИИ-анализ такой документ уйдёт пустым).
    has_text: bool


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tender_id: uuid.UUID
    text: str
    normalized_text: str | None
    criticality: str
    category: str | None
    verified_by_user: bool
    created_at: datetime


class ComplianceEntryOut(BaseModel):
    """Ячейка матрицы соответствия. `manufacturer_name`/`brand_name` подставляются сервисом:
    матрица всегда показывается по производителям, и отдавать голые идентификаторы значило бы
    заставить фронт делать второй запрос ради подписей колонок."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requirement_id: uuid.UUID
    manufacturer_id: uuid.UUID
    manufacturer_name: str
    status: str
    explanation: str | None
    source: str
    confidence: Decimal | None
    needs_human_review: bool


class WinPercentageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    manufacturer_id: uuid.UUID
    manufacturer_name: str
    is_mirtek: bool
    percentage: Decimal
    reason_summary: str | None
    requirements_total: int
    requirements_scored: int
    calculated_at: datetime


class ComplianceMatrixOut(BaseModel):
    """Матрица целиком — в том виде, в каком её рисует карточка тендера (раздел 5.6 ТЗ):
    строки-требования, колонки-производители, проценты снизу."""

    requirements: list[RequirementOut]
    entries: list[ComplianceEntryOut]
    win_percentages: list[WinPercentageOut]


class RelevanceUpdate(BaseModel):
    """Подтверждение/отклонение релевантности тендера человеком (раздел 5.6 ТЗ)."""

    relevance_status: str


class TenderPageOut(BaseModel):
    """Страница списка тендеров (раздел 5.6 ТЗ). `total` — сколько записей отвечает
    фильтрам целиком, а не сколько уместилось на странице: без него интерфейс не может
    показать ни число найденного, ни количество страниц."""

    items: list[TenderOut]
    total: int
    limit: int
    offset: int
    # Разбивка по статусам считается на сервере по всей выборке: колонки Kanban не могут
    # получить её из `items`, там лежит только текущая страница (см.
    # `tender_service.count_tenders_by_status`). Ключ `unclassified` — тендеры без статуса.
    status_counts: dict[str, int] = Field(default_factory=dict)


class TenderBoardOut(BaseModel):
    """Доска Kanban (раздел 5.6 ТЗ). Колонки набираются независимо друг от друга, поэтому
    у доски нет ни `offset`, ни общей страницы: `columns` — начало каждой колонки,
    `counts` — сколько всего в ней по текущим фильтрам, `total` — по всей выборке.

    Ключ и там, и там — статус тендера; пустой статус приходит как `unclassified`."""

    columns: dict[str, list[TenderOut]]
    counts: dict[str, int]
    total: int
    per_column: int


class TenderUpdate(BaseModel):
    """Исправление классификации человеком (раздел 5.6 ТЗ).

    Все поля необязательны, и `None` — осмысленное значение («снять классификацию»),
    поэтому применяются только те, что реально пришли в запросе: сервис получает
    `model_dump(exclude_unset=True)`, а не полный словарь с None вместо нетронутых полей.
    """

    model_config = ConfigDict(extra="forbid")

    tender_type: str | None = None
    region_organizer_code: str | None = None
    region_delivery_code: str | None = None
    federal_district_code: int | None = None
    okpd2_code: str | None = None
    relevance_status: str | None = None
    stage: str | None = None
    assignee_id: uuid.UUID | None = None


class TenderCommentCreate(BaseModel):
    text: str


class TenderHistoryOut(BaseModel):
    """Запись ленты изменений карточки. `field_label` — русская подпись поля: её даёт
    бэкенд, чтобы список редактируемых полей и их названия жили в одном месте."""

    id: uuid.UUID
    kind: str
    field_name: str | None
    field_label: str | None
    old_value: str | None
    new_value: str | None
    comment: str | None
    user_id: uuid.UUID | None
    user_name: str | None
    created_at: datetime


class RegionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    federal_district_code: int


class FederalDistrictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: int
    name: str


class TenderCardSection(BaseModel):
    """Раздел карточки закупки: заголовок и пары «поле — значение» в порядке страницы."""

    title: str
    fields: list[tuple[str, str]]


class TenderCardTable(BaseModel):
    title: str
    headers: list[str]
    rows: list[list[str]]


class TenderCardOut(BaseModel):
    """Карточка закупки в том виде, в каком её показывает сайт источника (раздел 5.6 ТЗ):
    сведения о заказчике и контактах, предоставление документации, лоты, изменения,
    протоколы, договоры, журнал событий."""

    sections: list[TenderCardSection]
    tables: dict[str, TenderCardTable]
    tab_urls: dict[str, str]
    fetched_at: datetime | None
    insights: dict | None = None


class InsightItemOut(BaseModel):
    title: str
    detail: str
    severity: str
    evidence: str | None = None


class TenderInsightsOut(BaseModel):
    """Разбор карточки моделью: риски, пробелы в данных и восстановленные значения."""

    summary: str
    risks: list[InsightItemOut]
    data_gaps: list[InsightItemOut]
    filled_fields: list[dict]
    checklist: list[str]
    generated_at: str | None = None
    applied_fields: list[str] = []


class StageUpdate(BaseModel):
    """Перевод тендера на другой этап пайплайна (раздел 5.6 ТЗ)."""

    stage: str


class AssigneeUpdate(BaseModel):
    """Ответственный за конкретный тендер. `None` — снять назначение."""

    assignee_id: uuid.UUID | None = None


class DocumentFlagsUpdate(BaseModel):
    """Отметки документа, которые ставит человек (раздел 5.6 ТЗ, вкладка «Документы»)."""

    model_config = ConfigDict(extra="forbid")

    is_priority_source: bool | None = None
    document_class: str | None = None


class EvidenceItemOut(BaseModel):
    """Ссылка, из которой сложилось число измерения (раздел 5.5.1 ТЗ, «Прослеживаемость»)."""

    type: str
    ref_id: str
    note: str | None = None


class WeakPointOut(BaseModel):
    severity: str
    severity_label: str
    text: str


class RecommendedStrategyOut(BaseModel):
    verdict: str
    price: str
    first_step: str


class AiProfileScoreOut(BaseModel):
    """AI-оценка по профилю целиком — в том виде, в каком её рисует шапка карточки.

    Измерения приходят объектами со своим числом, комментарием и списком обоснований:
    интерфейс обязан показать не только процент, но и то, из чего он получился.
    `history_score = null` — это «недостаточно данных», а не ноль (раздел 5.5.1 ТЗ).
    """

    id: uuid.UUID
    tender_id: uuid.UUID

    history_score: Decimal | None
    history_comment: str | None
    history_evidence: list[EvidenceItemOut]

    task_score: Decimal | None
    task_comment: str | None
    task_evidence: list[EvidenceItemOut]

    competencies_score: Decimal | None
    competencies_comment: str | None
    competencies_evidence: list[EvidenceItemOut]

    overall_score: Decimal | None
    summary: str | None
    verdict: str | None
    verdict_label: str | None
    weak_points: list[WeakPointOut]
    recommended_strategy: RecommendedStrategyOut | None
    similar_tender_ids: list[uuid.UUID]
    # Записи `company_participations`, на которые опирается History после уточнения
    # 03.09.2026: они, а не агрегаты, отвечают за «выигрывали ли мы такое раньше».
    participation_ids: list[uuid.UUID]
    company_profile_snapshot: dict | None
    calculated_at: datetime


class LicenseItem(BaseModel):
    """Допуск или лицензия компании. Поля необязательные: у СРО, лицензии ФСБ и сертификата
    ISO набор реквизитов разный, и требовать все от каждого — заставлять заполнять прочерки."""

    name: str
    number: str | None = None
    issued_at: str | None = None
    valid_until: str | None = None
    issuer: str | None = None


class PastProjectItem(BaseModel):
    """Реализованный проект — вход измерения Task (раздел 5.5.1 ТЗ)."""

    work_type: str
    customer: str | None = None
    volume: str | None = None
    year: int | None = None
    description: str | None = None


class CompanyProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    manufacturer_id: uuid.UUID
    legal_name: str | None
    inn: str | None
    kpp: str | None
    ogrn: str | None
    registration_date: date | None
    legal_address: str | None
    # {поле: {source, verified_by_user}} — откуда взялось значение и подтвердил ли его человек.
    field_sources: dict
    years_of_experience: int | None
    licenses: list[LicenseItem]
    past_projects: list[PastProjectItem]
    bank_requisites: dict | None
    letterhead_file_path: str | None
    updated_at: datetime
    # Хватает ли профиля для расчёта Task/Competencies — считает бэкенд, чтобы правило
    # «чем именно профиль считается заполненным» жило в одном месте.
    is_filled: bool
    # Без ИНН историю участий не запросить: реестр контрактов ищется именно по нему.
    has_inn: bool


class CompanyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = None
    inn: str | None = Field(default=None, max_length=20)
    kpp: str | None = Field(default=None, max_length=20)
    ogrn: str | None = Field(default=None, max_length=20)
    registration_date: date | None = None
    legal_address: str | None = None
    field_sources: dict | None = None
    years_of_experience: int | None = Field(default=None, ge=0, le=200)
    licenses: list[LicenseItem] | None = None
    past_projects: list[PastProjectItem] | None = None
    bank_requisites: dict | None = None


class ExtraSectionOut(BaseModel):
    """Один из девяти разделов вкладки «Дополнительно» (раздел 5.6 ТЗ)."""

    key: str
    title: str
    points: list[str]


class TenderExtraSectionsOut(BaseModel):
    sections: list[ExtraSectionOut]
    generated_at: str | None = None


class NicheStatisticsOut(BaseModel):
    """Данные вкладки «Расчёт» (раздел 5.6 ТЗ). Все поля могут быть пустыми: пока агрегаты
    по нише не собраны, вкладка показывает честное «анализ ещё не запускался», а не нули."""

    model_config = ConfigDict(from_attributes=True)

    okpd2_code: str
    region_code: str | None
    sample_size: int | None
    avg_participants: Decimal | None
    single_participant_share: Decimal | None
    median_price_reduction_pct: Decimal | None
    usual_submission_days: int | None
    top_winners: list[dict]
    source: str
    calculated_at: datetime


class SimilarTenderOut(BaseModel):
    """Строка вкладки «Похожие» (раздел 5.6 ТЗ)."""

    tender_id: uuid.UUID
    title: str
    customer_name: str | None
    price: Decimal | None
    publish_date: date | None
    similarity_score: Decimal
