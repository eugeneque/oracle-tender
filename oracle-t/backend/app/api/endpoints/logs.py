import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.job import BackgroundJob
from app.models.user import User
from app.schemas.job import BackgroundJobOut
from app.schemas.log import LogFacets, LogPage
from app.services import log_service

router = APIRouter(tags=["logs"])


@router.get("/logs", response_model=LogPage)
def get_logs(
    level: list[str] | None = Query(default=None, description="Уровни: INFO/WARNING/ERROR/CRITICAL"),
    component: str | None = Query(default=None),
    search: str | None = Query(default=None),
    hours: int | None = Query(default=None, ge=1, le=24 * 30, description="За последние N часов"),
    limit: int = Query(default=200, ge=1, le=log_service.MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> LogPage:
    """Журнал операций (раздел 5.9 ТЗ) — раздел «Логирование» в настройках.

    Доступен любому аутентифицированному пользователю: это журнал работы системы, а не
    персональных данных."""

    since = datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None
    return log_service.get_logs(
        db,
        levels=level,
        component=component,
        search=search,
        since=since,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/facets", response_model=LogFacets)
def get_log_facets(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> LogFacets:
    return log_service.get_facets(db)


@router.get("/jobs", response_model=list[BackgroundJobOut])
def get_jobs(
    tender_id: uuid.UUID | None = Query(default=None),
    kind: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[BackgroundJobOut]:
    """Состояние фоновых задач. Карточка тендера опрашивает этот эндпоинт с `tender_id`,
    пока её задача не завершится.

    `kind` нужен карточке отдельно от опроса: чтобы объяснить пустую вкладку («требований
    нет, потому что анализ ещё не запускали» против «анализ отработал и не нашёл ни одного»),
    она спрашивает последнюю задачу нужного вида. Без фильтра её пришлось бы искать в общем
    списке, где её могли вытеснить более поздние расчёты."""

    query = select(BackgroundJob)
    if tender_id is not None:
        query = query.where(BackgroundJob.tender_id == tender_id)
    if kind is not None:
        # Несколько видов через запятую: анализ документов выполняется и отдельной задачей,
        # и шагом полного разбора, а карточке нужен последний из них.
        kinds = [item.strip() for item in kind.split(",") if item.strip()]
        query = query.where(BackgroundJob.kind.in_(kinds))
    if active_only:
        query = query.where(BackgroundJob.status.in_(["queued", "running"]))
    rows = (
        db.execute(query.order_by(BackgroundJob.created_at.desc()).limit(limit)).scalars().all()
    )
    return [BackgroundJobOut.model_validate(job, from_attributes=True) for job in rows]
