import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.jobs import enqueue
from app.db.session import get_db
from app.models.job import JobKind
from app.models.market import NicheStatistics, SimilarTender
from app.models.tender import Tender
from app.models.tender_document import DOCUMENT_CLASS_LABELS, DocumentClass, TenderDocument
from app.models.user import User
from app.schemas.job import BackgroundJobOut
from app.schemas.tender import (
    AiProfileScoreOut,
    AssigneeUpdate,
    DocumentFlagsUpdate,
    ExtraSectionOut,
    ManualRequestOut,
    NicheStatisticsOut,
    SimilarTenderOut,
    StageUpdate,
    TenderCardOut,
    TenderExtraSectionsOut,
    TenderInsightsOut,
    ComplianceMatrixOut,
    RelevanceUpdate,
    RequirementOut,
    TenderCommentCreate,
    TenderDocumentOut,
    TenderHistoryOut,
    TenderBookmarkIn,
    TenderBookmarkOut,
    TenderOut,
    TenderBoardOut,
    TenderPageOut,
    TenderStatsOut,
    TenderUpdate,
)
from app.services.tender_edit_service import (
    FIELD_LABELS,
    TenderEditError,
    add_comment,
    list_history,
    set_relevance,
    set_stage,
    update_tender,
)
from app.services.analysis_service import (
    attach_analysis_fields,
    get_compliance_matrix,
    list_requirements,
)
from app.services import (
    bookmark_service,
    ai_profile_service,
    company_profile_service,
    similarity_service,
    tender_card_service,
    tender_insights,
)
from app.services.company_profile_service import CompanyProfileError
from app.services.manual_request_service import (
    ManualRequestError,
    UploadedFile,
    attach_uploaded_documents,
    create_manual_request,
)
from app.services.document_service import get_storage_root, sync_tender_documents
from app.services.yandex_ai_client import YandexAiNotConfiguredError
from app.services.tender_service import (
    DEFAULT_SORT,
    TenderFilters,
    count_tenders,
    count_tenders_by_stage,
    count_tenders_by_status,
    get_tender_by_id,
    list_board_columns,
    get_tender_stats,
    list_tenders,
)

router = APIRouter(prefix="/tenders", tags=["tenders"])


def tender_filters(  # noqa: PLR0913 - фильтры раздела 5.6 ТЗ, каждый отдельным query-параметром
    search: str | None = Query(default=None, max_length=200),
    source: list[str] | None = Query(default=None, description="Ключи источников (можно несколько)"),
    publish_date_from: date | None = None,
    publish_date_to: date | None = None,
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    hide_expired: bool = False,
    only_profile_relevant: bool = False,
    only_ai_selected: bool = False,
    region: list[str] | None = Query(default=None, description="Коды регионов (заказчика или поставки)"),
    federal_district: list[int] | None = Query(default=None),
    tender_type: list[str] | None = Query(default=None),
    tender_status: list[str] | None = Query(default=None),
    relevance_status: list[str] | None = Query(default=None),
    stage: list[str] | None = Query(default=None, description="Этапы внутреннего пайплайна"),
    assignee: list[uuid.UUID] | None = Query(default=None, description="Ответственные за тендер"),
    okpd2: str | None = Query(default=None, max_length=20, description="Код ОКПД2 или его начало"),
    win_percentage_min: Decimal | None = Query(default=None, ge=0, le=100),
    win_percentage_max: Decimal | None = Query(default=None, ge=0, le=100),
    ai_score_min: Decimal | None = Query(default=None, ge=0, le=100),
    ai_score_max: Decimal | None = Query(default=None, ge=0, le=100),
) -> TenderFilters:
    """Общий разбор фильтров для списка и доски.

    Одной зависимостью, а не копией параметров в каждом обработчике: список и Kanban обязаны
    отбирать один и тот же набор тендеров, и разошедшийся набор query-параметров — это
    расхождение, которое пользователь увидит как «в таблице есть, на доске нет»."""

    return TenderFilters(
        search=search,
        source_keys=source or [],
        publish_date_from=publish_date_from,
        publish_date_to=publish_date_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        price_min=price_min,
        price_max=price_max,
        hide_expired=hide_expired,
        only_profile_relevant=only_profile_relevant,
        only_ai_selected=only_ai_selected,
        region_codes=region or [],
        federal_district_codes=federal_district or [],
        tender_types=tender_type or [],
        statuses=tender_status or [],
        relevance_statuses=relevance_status or [],
        stages=stage or [],
        assignee_ids=assignee or [],
        okpd2_prefix=okpd2,
        win_percentage_min=win_percentage_min,
        win_percentage_max=win_percentage_max,
        ai_score_min=ai_score_min,
        ai_score_max=ai_score_max,
    )


@router.get("", response_model=TenderPageOut)
def get_tenders(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default=DEFAULT_SORT, description="Столбец сортировки"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    bookmarked: bool = Query(default=False, description="Только избранное текущего пользователя"),
    filters: TenderFilters = Depends(tender_filters),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenderPageOut:
    if bookmarked:
        filters.bookmarked_by_user_id = user.id
    tenders = list_tenders(
        db,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        descending=sort_dir == "desc",
        filters=filters,
    )
    # Процент победителя и число требований живут в отдельных таблицах (этапы 5-6), но
    # нужны на каждой карточке списка — досыпаются одним запросом на всю выдачу.
    return TenderPageOut(
        items=attach_analysis_fields(db, tenders, user_id=user.id),
        total=count_tenders(db, filters=filters),
        limit=limit,
        offset=offset,
        status_counts=count_tenders_by_status(db, filters=filters),
    )


@router.get("/board", response_model=TenderBoardOut)
def get_tenders_board(
    per_column: int = Query(default=20, ge=1, le=200, description="Карточек в одной колонке"),
    sort_by: str = Query(default=DEFAULT_SORT, description="Столбец сортировки"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    bookmarked: bool = Query(default=False, description="Только избранное текущего пользователя"),
    filters: TenderFilters = Depends(tender_filters),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenderBoardOut:
    """Доска Kanban по этапам пайплайна (раздел 5.6 ТЗ, решение 03.09.2026).

    Отдельный эндпоинт, а не страница списка: страница отдаёт сквозной срез по одной
    сортировке, и колонки достаются ей как придётся — вплоть до пустой доски при выдаче в
    сотни тендеров. Подробности в `tender_service.list_board_columns`.

    Колонки — `stage` («новая», «на проверке», «заявка подана», исходы), а не `status`
    площадки: доска показывает работу тендерного отдела. Состояние закупки на площадке
    осталось отдельным фильтром и бейджем карточки.
    """

    if bookmarked:
        filters.bookmarked_by_user_id = user.id
    columns = list_board_columns(
        db,
        per_column=per_column,
        sort_by=sort_by,
        descending=sort_dir == "desc",
        filters=filters,
    )
    # Проценты победителя досыпаются одним запросом на всю доску, а не по колонке.
    # `attach_analysis_fields` отдаёт словари, а не ORM-объекты, — раскладываем их обратно
    # по колонкам по идентификатору.
    enriched = attach_analysis_fields(
        db, [t for column in columns.values() for t in column], user_id=user.id
    )
    by_id = {row["id"]: row for row in enriched}

    counts = count_tenders_by_stage(db, filters=filters)
    return TenderBoardOut(
        columns={
            stage: [by_id[tender.id] for tender in column] for stage, column in columns.items()
        },
        counts=counts,
        total=sum(counts.values()),
        per_column=per_column,
    )


@router.get("/stats", response_model=TenderStatsOut)
def get_tenders_stats(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> dict:
    return get_tender_stats(db)


def _get_tender_or_404(db: Session, tender_id: uuid.UUID) -> Tender:
    tender = get_tender_by_id(db, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")
    return tender


def _read_uploads(uploads: list[UploadFile]) -> list[UploadedFile]:
    """Читает multipart-файлы в память. Пустые слоты формы (браузер отправляет `files` без
    выбранного файла как поле с пустым именем) пропускаются, а не превращаются в ошибку."""

    files: list[UploadedFile] = []
    for upload in uploads:
        if not upload.filename:
            continue
        files.append(UploadedFile(file_name=upload.filename, content=upload.file.read()))
    return files


@router.post("/manual", response_model=ManualRequestOut, status_code=status.HTTP_201_CREATED)
def create_manual(  # noqa: PLR0913 - поля формы заявки, каждое отдельным полем multipart
    title: str = Form(..., max_length=2000),
    customer_name: str | None = Form(default=None, max_length=500),
    price: Decimal | None = Form(default=None),
    application_end: date | None = Form(default=None),
    comment: str | None = Form(default=None, max_length=5000),
    run_analysis: bool = Form(default=True),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ManualRequestOut:
    """Ручная заявка: закупка, которую заказчик прислал напрямую (решение 15.09.2026).

    Multipart, а не JSON: вместе с полями приходят файлы — проект договора, ТЗ. Файлы
    разбираются сразу, а ИИ-анализ требований ставится фоновой задачей — той же, что и у
    собранных тендеров (`POST /tenders/{id}/analyze`), — если `run_analysis` не снят.
    """

    try:
        tender, documents = create_manual_request(
            db,
            title=title,
            customer_name=customer_name,
            price=price,
            application_end=application_end,
            comment=comment,
            files=_read_uploads(files),
            actor=user,
        )
    except ManualRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    job = None
    if run_analysis and documents:
        job = enqueue(db, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=user)

    return ManualRequestOut(
        tender=attach_analysis_fields(db, [tender])[0],
        documents=[_document_out(document) for document in documents],
        job=BackgroundJobOut.model_validate(job, from_attributes=True) if job else None,
    )


@router.get("/{tender_id}", response_model=TenderOut)
def get_tender(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Карточка тендера. Проходит через `attach_analysis_fields` так же, как список: без
    этого перечитывание карточки после анализа возвращало бы пустой процент победителя, и
    список, обновляемый из её ответа, терял бы уже посчитанное значение."""

    tender = _get_tender_or_404(db, tender_id)
    return attach_analysis_fields(db, [tender], user_id=user.id)[0]


@router.put("/{tender_id}/bookmark", response_model=TenderOut)
def put_tender_bookmark(
    tender_id: uuid.UUID,
    payload: TenderBookmarkIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Добавить закупку в своё избранное (повторный вызов обновляет заметку)."""

    tender = _get_tender_or_404(db, tender_id)
    bookmark_service.add(db, tender, user, note=payload.note if payload else None)
    return attach_analysis_fields(db, [tender], user_id=user.id)[0]


@router.delete("/{tender_id}/bookmark", response_model=TenderOut)
def delete_tender_bookmark(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tender = _get_tender_or_404(db, tender_id)
    bookmark_service.remove(db, tender, user)
    return attach_analysis_fields(db, [tender], user_id=user.id)[0]


@router.get("/{tender_id}/bookmark", response_model=TenderBookmarkOut | None)
def get_tender_bookmark(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Заметка избранного текущего пользователя по закупке; `null`, если её нет в избранном."""

    tender = _get_tender_or_404(db, tender_id)
    return bookmark_service.get(db, tender, user)


def _document_out(document: TenderDocument) -> TenderDocumentOut:
    """Документ с русской подписью класса. Подпись даёт бэкенд по той же причине, что и
    подписи полей истории: перечень классов и их названия должны жить в одном месте."""

    return TenderDocumentOut(
        id=document.id,
        file_name=document.file_name,
        file_type=document.file_type,
        parse_status=document.parse_status,
        parse_error=document.parse_error,
        downloaded_at=document.downloaded_at,
        has_text=document.has_text,
        document_class=document.document_class,
        document_class_label=DOCUMENT_CLASS_LABELS.get(document.document_class or ""),
        is_priority_source=document.is_priority_source,
    )


@router.get("/{tender_id}/documents", response_model=list[TenderDocumentOut])
def get_tender_documents(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Список документов тендера — при первом обращении запускает их скачивание и разбор
    (раздел 5.2 ТЗ), при повторных — отдаёт уже сохранённые записи мгновенно (см. докстринг
    `app/services/document_service.sync_tender_documents`)."""

    tender = _get_tender_or_404(db, tender_id)
    return [_document_out(document) for document in sync_tender_documents(db, tender)]


@router.post(
    "/{tender_id}/documents/upload",
    response_model=list[TenderDocumentOut],
    status_code=status.HTTP_201_CREATED,
)
def upload_tender_documents(
    tender_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TenderDocumentOut]:
    """Приложить файлы к тендеру вручную — к заявке или к собранной закупке, по которой
    заказчик прислал уточнённое ТЗ письмом.

    Перед загрузкой синхронизируется комплект с площадки: `sync_tender_documents` скачивает
    его только при пустом списке, и загруженный раньше времени файл иначе навсегда
    отменил бы скачивание документации самой закупки."""

    tender = _get_tender_or_404(db, tender_id)
    sync_tender_documents(db, tender)
    uploads = _read_uploads(files)
    if not uploads:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Не выбран ни один файл"
        )
    try:
        documents = attach_uploaded_documents(db, tender, uploads, actor=user)
    except ManualRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return [_document_out(document) for document in documents]


@router.get("/{tender_id}/documents/{document_id}/download")
def download_tender_document(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    tender = _get_tender_or_404(db, tender_id)
    documents = sync_tender_documents(db, tender)
    document = next((d for d in documents if d.id == document_id), None)
    if document is None or not document.storage_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден или ещё не скачан"
        )

    file_path = get_storage_root() / document.storage_path
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден на диске")

    return FileResponse(
        path=file_path,
        filename=document.file_name,
        media_type="application/octet-stream",
    )


@router.get("/{tender_id}/requirements", response_model=list[RequirementOut])
def get_tender_requirements(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Требования, извлечённые из документации (раздел 5.4 ТЗ). Только чтение — просмотр
    карточки не должен запускать обращения к модели."""

    tender = _get_tender_or_404(db, tender_id)
    return list_requirements(db, tender.id)


@router.get("/{tender_id}/compliance", response_model=ComplianceMatrixOut)
def get_tender_compliance(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ComplianceMatrixOut:
    """Матрица соответствия и проценты победителя (раздел 5.5 ТЗ)."""

    tender = _get_tender_or_404(db, tender_id)
    return get_compliance_matrix(db, tender)


@router.post("/{tender_id}/analyze", response_model=BackgroundJobOut)
def analyze(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BackgroundJobOut:
    """Этап 5: извлечение требований, критичности, типа конкурса, ОКПД2 и региона.

    Ставит фоновую задачу и сразу возвращает её состояние. Синхронно этот разбор занимал на
    крупном тендере десятки секунд: браузер держал запрос открытым, при обрыве связи
    результат терялся, а закрытая вкладка выглядела как «ничего не произошло». Теперь
    прогресс виден в карточке и в разделе «Логирование», а сбой повторяется автоматически
    (раздел 5.9 ТЗ).

    Ненастроенное подключение к Yandex AI Studio — ошибка конфигурации, а не сбой сервера:
    её текст окажется в `message` задачи, туда же смотрит интерфейс."""

    tender = _get_tender_or_404(db, tender_id)
    job = enqueue(db, kind=JobKind.TENDER_ANALYSIS, tender=tender, actor=user)
    return BackgroundJobOut.model_validate(job, from_attributes=True)


@router.post("/{tender_id}/evaluate", response_model=BackgroundJobOut)
def evaluate(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BackgroundJobOut:
    """Этап 6: матрица соответствия и расчёт процента победителя — тоже фоновой задачей
    (перебор всех производителей по всем требованиям заведомо дольше анализа)."""

    tender = _get_tender_or_404(db, tender_id)
    job = enqueue(db, kind=JobKind.TENDER_EVALUATION, tender=tender, actor=user)
    return BackgroundJobOut.model_validate(job, from_attributes=True)


@router.patch("/{tender_id}", response_model=TenderOut)
def update(
    tender_id: uuid.UUID,
    payload: TenderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Исправление классификации человеком (раздел 5.6 ТЗ): тип конкурса, регионы, ОКПД2,
    релевантность. Каждое изменение попадает в историю карточки и в журнал."""

    tender = _get_tender_or_404(db, tender_id)
    changes = payload.model_dump(exclude_unset=True)
    try:
        update_tender(db, tender, changes, actor=user)
    except TenderEditError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return attach_analysis_fields(db, [tender])[0]


@router.patch("/{tender_id}/relevance", response_model=TenderOut)
def set_tender_relevance(
    tender_id: uuid.UUID,
    payload: RelevanceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Подтверждение релевантности / отметка «неактуально» (раздел 5.6 ТЗ) — с записью в
    историю и журнал, чтобы решение было прослеживаемым."""

    tender = _get_tender_or_404(db, tender_id)
    try:
        set_relevance(db, tender, payload.relevance_status, actor=user)
    except TenderEditError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return attach_analysis_fields(db, [tender])[0]


def _history_out(entry, user_name: str | None) -> TenderHistoryOut:
    return TenderHistoryOut(
        id=entry.id,
        kind=entry.kind,
        field_name=entry.field_name,
        field_label=FIELD_LABELS.get(entry.field_name) if entry.field_name else None,
        old_value=entry.old_value,
        new_value=entry.new_value,
        comment=entry.comment,
        user_id=entry.user_id,
        user_name=user_name,
        created_at=entry.created_at,
    )


@router.get("/{tender_id}/history", response_model=list[TenderHistoryOut])
def get_tender_history(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[TenderHistoryOut]:
    """История изменений карточки: кто, что и когда поправил (раздел 5.6 ТЗ)."""

    tender = _get_tender_or_404(db, tender_id)
    return [_history_out(entry, user_name) for entry, user_name in list_history(db, tender.id)]


@router.post(
    "/{tender_id}/comments",
    response_model=TenderHistoryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_tender_comment(
    tender_id: uuid.UUID,
    payload: TenderCommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenderHistoryOut:
    """Комментарий пользователя к тендеру (раздел 5.6 ТЗ) — в ту же ленту, что и правки."""

    tender = _get_tender_or_404(db, tender_id)
    try:
        entry = add_comment(db, tender, payload.text, actor=user)
    except TenderEditError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _history_out(entry, user.full_name)


@router.get("/{tender_id}/card", response_model=TenderCardOut)
def get_tender_card(
    tender_id: uuid.UUID,
    refresh: bool = Query(default=False, description="Перечитать карточку с сайта источника"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenderCardOut:
    """Полная карточка закупки с сайта источника (раздел 5.6 ТЗ).

    При первом обращении карточка забирается с сайта и сохраняется — страница ЕИС отвечает
    секунды, а тендер открывают многократно. Попутно из неё заполняются пустые поля самого
    тендера: регион заказчика, способ закупки, ОКПД2 — всё это на странице есть, но в строку
    реестра, из которой собирается список, не попадает.
    """

    tender = _get_tender_or_404(db, tender_id)
    card = tender_card_service.sync_card(db, tender, actor=user, force=refresh)
    if card is None:
        # Карточки нет только у закупок без номера в ЕИС (внутренние номера коммерческих
        # площадок) — это не ошибка, просто показывать нечего.
        return TenderCardOut(sections=[], tables={}, tab_urls={}, fetched_at=None)

    payload = card.payload or {}
    return TenderCardOut(
        sections=payload.get("sections", []),
        tables=payload.get("tables", {}),
        tab_urls=payload.get("tab_urls", {}),
        fetched_at=card.fetched_at,
        insights=payload.get("insights"),
    )


@router.post("/{tender_id}/insights", response_model=TenderInsightsOut)
def build_tender_insights(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenderInsightsOut:
    """ИИ-разбор карточки: риски, пробелы в данных и восстановленные значения (раздел 5.4 ТЗ).

    Выполняется синхронно, в отличие от анализа документации: здесь один запрос к модели по
    короткому тексту карточки — секунды, а не минуты, и результат нужен сразу на экране.
    """

    tender = _get_tender_or_404(db, tender_id)
    card = tender_card_service.sync_card(db, tender, actor=user)

    try:
        result = tender_insights.build_insights(db, tender, card, actor=user)
    except YandexAiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сбой модели показываем текстом, а не 500-й
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Модель не ответила: {exc}",
        ) from exc

    applied = tender_insights.apply_filled_fields(db, tender, result)
    tender_insights.store_insights(db, tender, result)

    return TenderInsightsOut(
        **result.model_dump(),
        generated_at=(tender_insights.get_stored_insights(db, tender) or {}).get("generated_at"),
        applied_fields=applied,
    )


# --- AI-оценка по профилю (раздел 5.5.1 ТЗ) -------------------------------------------


@router.get("/{tender_id}/ai-score", response_model=AiProfileScoreOut | None)
def get_ai_score(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Текущая AI-оценка по профилю или `null`, если она ещё не считалась.

    Именно `null`, а не 404: «оценки нет» — обычное состояние только что собранного
    тендера, и карточка на него отвечает кнопкой «Рассчитать», а не сообщением об ошибке.
    """

    tender = _get_tender_or_404(db, tender_id)
    score = ai_profile_service.get_current(db, tender.id)
    return ai_profile_service.serialize(db, score) if score is not None else None


@router.get("/{tender_id}/ai-score/history", response_model=list[AiProfileScoreOut])
def get_ai_score_history(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Все пересчёты оценки, свежие первыми: после правки профиля компании цифры меняются,
    и карточка должна показывать, что именно изменилось и когда."""

    tender = _get_tender_or_404(db, tender_id)
    return [
        ai_profile_service.serialize(db, score)
        for score in ai_profile_service.list_history(db, tender.id)
    ]


@router.post("/{tender_id}/ai-score", response_model=BackgroundJobOut)
def compute_ai_score(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BackgroundJobOut:
    """Ставит фоновую задачу расчёта AI-оценки (раздел 5.5.1 ТЗ).

    Фоном, а не синхронно: перед самой оценкой обновляются девять разделов вкладки
    «Дополнительно», и это три-четыре обращения к модели подряд.

    Незаполненный профиль компании ловится до постановки задачи — иначе пользователь
    увидел бы упавшую фоновую задачу вместо понятной подсказки «заполните профиль».
    """

    tender = _get_tender_or_404(db, tender_id)
    try:
        company_profile_service.require_filled(db)
    except CompanyProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    job = enqueue(db, kind=JobKind.AI_PROFILE_SCORE, tender=tender, actor=user)
    return BackgroundJobOut.model_validate(job, from_attributes=True)


@router.patch("/{tender_id}/stage", response_model=TenderOut)
def update_stage(
    tender_id: uuid.UUID,
    payload: StageUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Перевод тендера на другой этап пайплайна — перетаскиванием на доске или из карточки."""

    tender = _get_tender_or_404(db, tender_id)
    try:
        set_stage(db, tender, payload.stage, actor=user)
    except TenderEditError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return attach_analysis_fields(db, [tender])[0]


@router.patch("/{tender_id}/assignee", response_model=TenderOut)
def update_assignee(
    tender_id: uuid.UUID,
    payload: AssigneeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ответственный за конкретный тендер (раздел 5.6 ТЗ).

    Отдельно от справочника «регион → ответственный»: справочник задаёт умолчание по
    региону, а работу по конкретной закупке могут передать другому человеку."""

    tender = _get_tender_or_404(db, tender_id)
    try:
        update_tender(db, tender, {"assignee_id": payload.assignee_id}, actor=user)
    except TenderEditError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return attach_analysis_fields(db, [tender])[0]


# --- вкладка «Дополнительно» (раздел 5.6 ТЗ) -------------------------------------------


def _extra_sections_out(stored: dict | None) -> TenderExtraSectionsOut:
    """Собирает ответ так, чтобы все девять разделов присутствовали всегда.

    Раздел, по которому модель ничего не нашла, приходит с пустым списком, а не пропадает:
    пустой раздел в карточке означает «в документации об этом не сказано», и это сведение,
    а не отсутствие сведений.
    """

    sections = (stored or {}).get("sections") or {}
    return TenderExtraSectionsOut(
        sections=[
            ExtraSectionOut(key=key, title=title, points=sections.get(key) or [])
            for key, title in tender_insights.EXTRA_SECTIONS.items()
        ],
        generated_at=(stored or {}).get("generated_at"),
    )


@router.get("/{tender_id}/extra-sections", response_model=TenderExtraSectionsOut)
def get_extra_sections(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> TenderExtraSectionsOut:
    """Сохранённые девять разделов вкладки «Дополнительно»."""

    tender = _get_tender_or_404(db, tender_id)
    return _extra_sections_out(tender_insights.get_stored_extra_sections(db, tender))


@router.post("/{tender_id}/extra-sections", response_model=TenderExtraSectionsOut)
def build_extra_sections(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenderExtraSectionsOut:
    """Извлекает разделы заново (два обращения к модели, раздел 5.6 ТЗ)."""

    tender = _get_tender_or_404(db, tender_id)
    card = tender_card_service.sync_card(db, tender, actor=user)
    try:
        sections = tender_insights.build_extra_sections(db, tender, card, actor=user)
    except YandexAiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not sections:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Модель не вернула ни одного раздела — попробуйте ещё раз",
        )
    tender_insights.store_extra_sections(db, tender, sections)
    return _extra_sections_out(tender_insights.get_stored_extra_sections(db, tender))


# --- вкладки «Расчёт» и «Похожие» -------------------------------------------------------


@router.get("/{tender_id}/niche-statistics", response_model=NicheStatisticsOut | None)
def get_niche_statistics(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Статистика по нише (ОКПД2 + регион) для вкладки «Расчёт» (раздел 5.6 ТЗ).

    `null`, пока агрегаты не собраны: вкладка показывает «анализ ещё не запускался», а не
    нули — нулевая медиана снижения цены выглядела бы как факт, которого никто не измерял.
    Регион ищется сначала точным совпадением, потом по всей стране — общероссийский агрегат
    лучше, чем пустая вкладка.
    """

    tender = _get_tender_or_404(db, tender_id)
    if not tender.okpd2_code:
        return None

    region_code = tender.region_delivery_code or tender.region_organizer_code
    query = select(NicheStatistics).where(NicheStatistics.okpd2_code == tender.okpd2_code)
    record = None
    if region_code:
        record = db.scalar(query.where(NicheStatistics.region_code == region_code))
    if record is None:
        record = db.scalar(query.where(NicheStatistics.region_code.is_(None)))
    return record


@router.get("/{tender_id}/similar", response_model=list[SimilarTenderOut])
def get_similar_tenders(
    tender_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[SimilarTenderOut]:
    """Похожие тендеры по близости текста (раздел 5.6 ТЗ, вкладка «Похожие»).

    Пока эмбеддинги не посчитаны, список пуст — и интерфейс показывает то же честное
    «недостаточно данных», что и измерение History, а не молчаливую пустоту.
    """

    tender = _get_tender_or_404(db, tender_id)
    rows = db.execute(
        select(SimilarTender.similarity_score, Tender)
        .join(Tender, Tender.id == SimilarTender.similar_tender_id)
        .where(SimilarTender.tender_id == tender.id)
        .order_by(SimilarTender.similarity_score.desc())
        .limit(limit)
    ).all()
    return [
        SimilarTenderOut(
            tender_id=similar.id,
            title=similar.title,
            customer_name=similar.customer_name,
            price=similar.price,
            publish_date=similar.publish_date,
            similarity_score=score,
        )
        for score, similar in rows
    ]


@router.post("/{tender_id}/similar", response_model=list[SimilarTenderOut])
def refresh_similar_tenders(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SimilarTenderOut]:
    """Пересчитывает вектор тендера и список похожих (раздел 5.5.1 ТЗ).

    Синхронно: это один вызов эмбеддингов и перебор уже сохранённых векторов — секунды, а
    не минуты, в отличие от разбора документации.
    """

    tender = _get_tender_or_404(db, tender_id)
    try:
        similarity_service.refresh_similar(db, tender, actor=user)
    except YandexAiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return get_similar_tenders(tender_id, limit=10, db=db, _user=user)


@router.patch("/{tender_id}/documents/{document_id}", response_model=TenderDocumentOut)
def update_document_flags(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: DocumentFlagsUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> TenderDocumentOut:
    """Звёздочка «приоритетный источник» и исправление класса документа (раздел 5.6 ТЗ).

    Приоритет учитывается при разборе: помеченный файл уходит в модель первым и не попадает
    под обрезку по длине контекста — а именно из-за обрезки техническое задание раньше
    могло не дойти до анализа.
    """

    tender = _get_tender_or_404(db, tender_id)
    document = db.get(TenderDocument, document_id)
    if document is None or document.tender_id != tender.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден у этого тендера"
        )

    changes = payload.model_dump(exclude_unset=True)
    if "document_class" in changes and changes["document_class"] is not None:
        allowed = {item.value for item in DocumentClass}
        if changes["document_class"] not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Недопустимый класс документа: {changes['document_class']}",
            )
    for field_name, value in changes.items():
        setattr(document, field_name, value)
    db.commit()
    db.refresh(document)
    return _document_out(document)
