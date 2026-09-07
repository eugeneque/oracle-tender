"""Выгрузка данных в Excel (раздел 5.7 ТЗ).

Эндпоинт принимает те же фильтры, что и список тендеров, и отдаёт файл потоком. Отдельный
роутер, а не метод внутри `/tenders`: выгрузка — это самостоятельная функция раздела 5.7,
и в неё позже добавятся другие отчёты (например, по каталогу продукции).
"""

import urllib.parse
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services import bitrix_service
from app.services.export_service import export_tenders
from app.services.tender_service import TenderFilters

router = APIRouter(prefix="/export", tags=["export"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Верхняя граница строк в одном файле. Excel держит миллион строк, но выгрузка в 5 тысяч
# уже открывается заметно дольше, а осмысленный отчёт — это отфильтрованный срез, а не вся
# база. Значение можно поднять параметром, если понадобится полный дамп.
DEFAULT_EXPORT_LIMIT = 5000


@router.get("/tenders.xlsx")
def get_tenders_xlsx(  # noqa: PLR0913 - фильтры раздела 5.6 ТЗ, каждый отдельным параметром
    limit: int = Query(default=DEFAULT_EXPORT_LIMIT, ge=1, le=50000),
    search: str | None = Query(default=None, max_length=200),
    source: list[str] | None = Query(default=None),
    publish_date_from: date | None = None,
    publish_date_to: date | None = None,
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    hide_expired: bool = False,
    only_profile_relevant: bool = False,
    region: list[str] | None = Query(default=None),
    federal_district: list[int] | None = Query(default=None),
    tender_type: list[str] | None = Query(default=None),
    tender_status: list[str] | None = Query(default=None),
    relevance_status: list[str] | None = Query(default=None),
    okpd2: str | None = Query(default=None, max_length=20),
    win_percentage_min: Decimal | None = Query(default=None, ge=0, le=100),
    win_percentage_max: Decimal | None = Query(default=None, ge=0, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    filters = TenderFilters(
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
        region_codes=region or [],
        federal_district_codes=federal_district or [],
        tender_types=tender_type or [],
        statuses=tender_status or [],
        relevance_statuses=relevance_status or [],
        okpd2_prefix=okpd2,
        win_percentage_min=win_percentage_min,
        win_percentage_max=win_percentage_max,
    )

    content, file_name, rows = export_tenders(db, filters, user, limit=limit)

    # Имя файла латиницей, поэтому хватило бы и простого filename, но filename* оставлен
    # намеренно: как только в имя попадёт период с русскими названиями месяцев, браузеры
    # без RFC 5987 отдадут пользователю мусор вместо имени.
    quoted = urllib.parse.quote(file_name)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{file_name}\"; filename*=UTF-8''{quoted}",
            # Фронту нужно показать «выгружено N строк», а тело ответа — бинарник.
            "X-Exported-Rows": str(rows),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Exported-Rows",
        },
    )


@router.get("/bitrix24-leads.csv")
def get_bitrix_leads_csv(
    limit: int = Query(default=DEFAULT_EXPORT_LIMIT, ge=1, le=50000),
    only_relevant: bool = Query(
        default=True, description="Исключить закупки, отклонённые вручную"
    ),
    min_win_percentage: Decimal | None = Query(default=None, ge=0, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Выгрузка тендеров в виде лидов Bitrix24 (раздел 5.10 ТЗ, Этап 13).

    Отдельный файл, а не лист в основной выгрузке: у отчёта по Приложению D и у импорта в
    CRM разные читатели и разные наборы полей, и смешение приводило бы к тому, что при
    импорте в Bitrix24 половину колонок приходится вручную пропускать.
    """

    filters = TenderFilters()
    if only_relevant:
        filters.relevance_statuses = ["new", "confirmed"]
    if min_win_percentage is not None:
        filters.win_percentage_min = min_win_percentage

    content, rows = bitrix_service.build_leads_csv(db, filters, limit=limit, actor=user)
    file_name = bitrix_service.build_file_name()

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{file_name}\"; "
                f"filename*=UTF-8\'\'{urllib.parse.quote(file_name)}"
            ),
            "X-Exported-Rows": str(rows),
        },
    )
