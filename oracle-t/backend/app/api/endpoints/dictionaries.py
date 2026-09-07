"""Справочники для фильтров интерфейса (раздел 5.6 ТЗ — Этап 7).

Регионы и федеральные округа (Приложения G, H ТЗ) заполняются сидом и меняются раз в
пятилетку, поэтому отдаются целиком, без пагинации и фильтров: список из 85 строк фронт
кэширует у себя и рисует из него все выпадающие списки.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.region import FederalDistrict, Region, RegionResponsible
from app.models.user import User
from app.schemas.analytics import RegionResponsibleOut, RegionResponsibleUpdate
from app.schemas.tender import FederalDistrictOut, RegionOut
from app.services.audit import log_action

router = APIRouter(prefix="/dictionaries", tags=["dictionaries"])


@router.get("/regions", response_model=list[RegionOut])
def get_regions(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[Region]:
    return list(db.scalars(select(Region).order_by(Region.name)))


@router.get("/federal-districts", response_model=list[FederalDistrictOut])
def get_federal_districts(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[FederalDistrict]:
    return list(db.scalars(select(FederalDistrict).order_by(FederalDistrict.code)))


@router.get("/region-responsibles", response_model=list[RegionResponsibleOut])
def get_region_responsibles(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[dict]:
    """Справочник «регион → ответственный/руководитель» (раздел 5.6 ТЗ).

    Отдаются только заполненные назначения, а не все 89 регионов: пустые строки в этом
    списке — шум, а форма редактирования выбирает регион из общего справочника регионов.
    """

    rows = db.execute(
        select(RegionResponsible, Region.name)
        .outerjoin(Region, Region.code == RegionResponsible.region_code)
        .order_by(Region.name)
    ).all()
    return [
        {
            "region_code": row.region_code,
            "region_name": region_name,
            "responsible_name": row.responsible_name,
            "manager_name": row.manager_name,
            "updated_at": row.updated_at,
        }
        for row, region_name in rows
    ]


@router.put("/region-responsibles/{region_code}", response_model=RegionResponsibleOut)
def put_region_responsible(
    region_code: str,
    payload: RegionResponsibleUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """Назначение ответственного по региону. PUT, а не PATCH: запись создаётся при первом
    назначении, и раздельные «создать/изменить» здесь только усложнили бы форму."""

    region = db.get(Region, region_code)
    if region is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Регион не найден в справочнике"
        )

    record = db.get(RegionResponsible, region_code)
    if record is None:
        record = RegionResponsible(region_code=region_code)
        db.add(record)

    record.responsible_name = (payload.responsible_name or "").strip() or None
    record.manager_name = (payload.manager_name or "").strip() or None
    record.updated_by_id = admin.id

    log_action(
        db,
        component="dictionaries",
        action=f"set_region_responsible:{region_code}",
        result="success",
        details=f"Ответственный: {record.responsible_name or '—'}; руководитель: {record.manager_name or '—'}",
        user_id=admin.id,
    )
    db.commit()
    db.refresh(record)

    return {
        "region_code": record.region_code,
        "region_name": region.name,
        "responsible_name": record.responsible_name,
        "manager_name": record.manager_name,
        "updated_at": record.updated_at,
    }
