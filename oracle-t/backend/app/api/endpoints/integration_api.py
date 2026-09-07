"""Внешний REST API для получения данных о тендерах (раздел 5.10 ТЗ, Этап 13).

Отдельный роутер и отдельная аутентификация — по ключу в заголовке `X-API-Key`, а не по
пользовательскому JWT: интеграции нужен постоянный доступ без входа по паролю и без
восьмичасового срока жизни токена.

Ответы намеренно «плоские» и самодостаточные: интеграция на стороне Bitrix24 не должна
ходить в наш справочник регионов, чтобы расшифровать код, или считать процент победителя по
матрице. Всё, что нужно для создания лида, приходит одним объектом — включая уже собранный
маппинг на поля CRM (`/leads`).

Реальных вызовов к Bitrix24 здесь нет: ТЗ выносит их во вторую очередь. Это именно
источник данных для будущей интеграции.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.analysis import ComplianceMatrixEntry, Requirement, WinPercentage
from app.models.api_client import ApiClient
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender
from app.schemas.integration import (
    IntegrationLeadPage,
    IntegrationTenderDetailOut,
    IntegrationTenderPage,
)
from app.services import bitrix_service
from app.services.tender_service import TenderFilters, apply_tender_filters

router = APIRouter(prefix="/integration/v1", tags=["integration"])

MAX_PAGE_SIZE = 500


def require_api_client(
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ApiClient:
    from app.services.api_client_service import authenticate

    client = authenticate(db, x_api_key)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отозванный ключ доступа. Ключ передаётся в заголовке X-API-Key.",
        )
    return client


def parse_updated_since(value: str | None) -> datetime | None:
    """Разбирает момент времени из query-параметра.

    Принимается не `datetime`, а строка, потому что смещение часового пояса в ISO 8601
    пишется через `+` («2026-09-01T12:00:00+03:00»), а `+` в query-строке означает пробел.
    Клиент, забывший про URL-кодирование, получал бы 422 с невнятным «invalid datetime» —
    ошибка, на поиск которой уходит вечер. Восстанавливаем пробел обратно в `+` (в корректной
    дате пробела быть не может) и принимаем суффикс `Z`.
    """

    if value is None or not value.strip():
        return None

    normalized = value.strip().replace(" ", "+")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Параметр updated_since должен быть моментом времени в формате ISO 8601, "
                "например 2026-09-01T12:00:00Z"
            ),
        ) from exc

    # Наивное время считаем UTC: сравнение с `updated_at` (timestamptz) иначе упадёт.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _filters(
    updated_since: datetime | None,
    only_relevant: bool,
    min_win_percentage: float | None,
) -> TenderFilters:
    """Фильтры выборки для интеграции.

    `updated_since` — главный параметр: интеграция забирает изменения порциями, а не всю
    базу каждый раз. Остальные два отражают типичный сценарий CRM — заводить лиды только по
    тем закупкам, которые действительно интересны.
    """

    filters = TenderFilters()
    if only_relevant:
        filters.relevance_statuses = ["new", "confirmed"]
    if min_win_percentage is not None:
        filters.win_percentage_min = Decimal(str(min_win_percentage))
    return filters


@router.get("/tenders", response_model=IntegrationTenderPage)
def list_tenders_for_integration(
    updated_since: str | None = Query(
        default=None,
        description=(
            "Только тендеры, изменённые после указанного момента (ISO 8601, например "
            "2026-09-01T12:00:00Z)"
        ),
    ),
    only_relevant: bool = Query(default=True, description="Исключить отклонённые вручную"),
    min_win_percentage: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _client: ApiClient = Depends(require_api_client),
) -> IntegrationTenderPage:
    """Список тендеров для внешней системы."""

    since = parse_updated_since(updated_since)
    query = apply_tender_filters(
        select(Tender), _filters(since, only_relevant, min_win_percentage)
    )
    if since is not None:
        query = query.where(Tender.updated_at > since)

    total = len(db.scalars(query).all())
    rows = db.scalars(
        query.order_by(Tender.updated_at.desc()).limit(limit).offset(offset)
    ).all()

    return IntegrationTenderPage(
        items=[_tender_payload(db, tender) for tender in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/tenders/{tender_id}", response_model=IntegrationTenderDetailOut)
def get_tender_for_integration(
    tender_id: uuid.UUID,
    db: Session = Depends(get_db),
    _client: ApiClient = Depends(require_api_client),
) -> IntegrationTenderDetailOut:
    """Полная карточка: тендер, требования и оценки соответствия по производителям."""

    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тендер не найден")

    requirements = db.scalars(
        select(Requirement).where(Requirement.tender_id == tender.id).order_by(Requirement.created_at)
    ).all()

    percentages = db.execute(
        select(WinPercentage, Manufacturer)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True))
        .order_by(Manufacturer.is_mirtek.desc(), WinPercentage.percentage.desc())
    ).all()

    entries_count = len(
        db.scalars(
            select(ComplianceMatrixEntry.id).where(ComplianceMatrixEntry.tender_id == tender.id)
        ).all()
    )

    return IntegrationTenderDetailOut(
        **_tender_payload(db, tender).model_dump(),
        requirements=[
            {
                "id": requirement.id,
                "text": requirement.text,
                "criticality": requirement.criticality,
                "category": requirement.category,
                "verified_by_user": requirement.verified_by_user,
            }
            for requirement in requirements
        ],
        win_percentages=[
            {
                "manufacturer": manufacturer.name,
                "is_mirtek": manufacturer.is_mirtek,
                "percentage": float(percentage.percentage),
                "requirements_total": percentage.requirements_total,
                "requirements_scored": percentage.requirements_scored,
            }
            for percentage, manufacturer in percentages
        ],
        compliance_entries_count=entries_count,
    )


@router.get("/leads", response_model=IntegrationLeadPage)
def list_leads_for_integration(
    updated_since: str | None = Query(default=None),
    only_relevant: bool = Query(default=True),
    min_win_percentage: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _client: ApiClient = Depends(require_api_client),
) -> IntegrationLeadPage:
    """Те же тендеры, уже разложенные по полям лида Bitrix24.

    Отдельный эндпоинт, а не параметр формата у `/tenders`: маппинг на CRM — это решение о
    том, что считать названием лида, стадией и ответственным, и держать его на стороне
    интеграции значило бы дублировать его в каждой системе, которая к нам подключится.
    """

    since = parse_updated_since(updated_since)
    query = apply_tender_filters(
        select(Tender), _filters(since, only_relevant, min_win_percentage)
    )
    if since is not None:
        query = query.where(Tender.updated_at > since)

    rows = db.scalars(query.order_by(Tender.updated_at.desc()).limit(limit)).all()

    return IntegrationLeadPage(
        items=[bitrix_service.build_lead(db, tender) for tender in rows],
        fields=[{"key": key, "title": title} for key, title in bitrix_service.LEAD_FIELDS],
        total=len(rows),
    )


def _tender_payload(db: Session, tender: Tender):
    from app.schemas.integration import IntegrationTenderOut

    win_percentage = db.execute(
        select(WinPercentage.percentage)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(
            WinPercentage.tender_id == tender.id,
            Manufacturer.is_mirtek.is_(True),
            WinPercentage.is_current.is_(True),
        )
    ).scalar_one_or_none()

    return IntegrationTenderOut(
        id=tender.id,
        external_id=tender.external_id,
        title=tender.title,
        source=tender.source.name if tender.source else "",
        source_key=tender.source.key if tender.source else "",
        source_url=tender.source_url,
        customer_name=tender.customer_name,
        organizer_name=tender.organizer_name,
        procurement_method=tender.procurement_method,
        status=tender.status,
        relevance_status=tender.relevance_status,
        price=float(tender.price) if tender.price is not None else None,
        currency=tender.currency,
        publish_date=tender.publish_date,
        application_start=tender.application_start,
        application_end=tender.application_end,
        okpd2_code=tender.okpd2_code,
        tender_type=tender.tender_type,
        region_organizer_code=tender.region_organizer_code,
        region_delivery_code=tender.region_delivery_code,
        federal_district_code=tender.federal_district_code,
        ai_comment=tender.ai_comment,
        win_percentage=float(win_percentage) if win_percentage is not None else None,
        created_at=tender.created_at,
        updated_at=tender.updated_at,
    )


@router.get("/health")
def integration_health(_client: ApiClient = Depends(require_api_client)) -> dict:
    """Проверка ключа: интеграции нужен способ убедиться, что доступ жив, не выгружая данные."""

    return {
        "status": "ok",
        "server_time": datetime.now(timezone.utc).isoformat(),
        # Подсказка о типичном шаге инкрементальной выборки: сутки назад — безопасное окно
        # для первого запроса после простоя.
        "suggested_updated_since": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    }
