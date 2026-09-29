import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import jobs
from app.core.jobs import queue_info
from app.db.session import get_db
from app.models.job import BackgroundJob
from app.models.user import User, UserRole
from app.schemas.job import BackgroundJobOut, JobQueueItem, UserJobQueue
from app.schemas.log import LogFacets, LogPage
from app.services import job_queue_service, log_service, tender_twins

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


@router.get("/logs/export")
def export_logs(
    hours: int = Query(default=24, ge=1, le=24 * 30, description="За последние N часов"),
    format: Literal["txt", "md"] = Query(default="txt"),
    level: list[str] | None = Query(default=None),
    component: str | None = Query(default=None),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Выгрузка журнала за период файлом — страница «Логирование», кнопки «За час / сутки /
    неделю». Фильтры те же, что у списка, чтобы в файл попадало то, что видно на экране."""

    content, file_name, rows = log_service.export_logs(
        db,
        hours=hours,
        fmt=format,
        actor=user,
        levels=level,
        component=component,
        search=search,
    )
    media_type = "text/markdown" if format == "md" else "text/plain"
    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{file_name}\"; "
                f"filename*=UTF-8''{urllib.parse.quote(file_name)}"
            ),
            "X-Exported-Rows": str(rows),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Exported-Rows",
        },
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
        # Задачи разбора идут по записи, где хранится разбор закупки (tender_twins): карточка
        # записи из Госплана должна видеть разбор, запущенный с записи из ЕИС.
        subject_id = tender_twins.subject_ids(db, [tender_id]).get(tender_id, tender_id)
        query = query.where(BackgroundJob.tender_id.in_({tender_id, subject_id}))
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
    info = queue_info(db, list(rows))
    return [
        BackgroundJobOut.model_validate(job, from_attributes=True).model_copy(
            update=(
                {"queue_reason": info[job.id][0], "queue_ahead": info[job.id][1]}
                if job.id in info
                else {}
            )
        )
        for job in rows
    ]


@router.get("/jobs/queue", response_model=list[UserJobQueue])
def get_job_queues(
    scope: Literal["mine", "all"] = Query(default="mine"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[UserJobQueue]:
    """Очереди разборов (29.09.2026): у каждого пользователя разбирается одна закупка за раз,
    остальные ждут. `mine` — своя очередь (любой пользователь), `all` — очереди всех
    (администратор, страница «Логирование»)."""

    if scope == "all":
        _require_admin(user)
    return job_queue_service.user_queues(db, user_id=user.id, everyone=scope == "all")


@router.get("/jobs/launched", response_model=list[JobQueueItem])
def get_launched_jobs(
    user_id: uuid.UUID | None = Query(default=None),
    system: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[JobQueueItem]:
    """Что запускал пользователь — последние задачи любого статуса. Свои — любой
    пользователь; чужие и задачи расписания (`system=true`) — администратор."""

    target = None if system else (user_id or user.id)
    if target != user.id:
        _require_admin(user)
    return job_queue_service.launched_by(db, target, limit=limit)


@router.post("/jobs/{job_id}/cancel", response_model=BackgroundJobOut)
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BackgroundJobOut:
    """Убрать разбор из очереди (29.09.2026). Ожидающий не начнётся, идущий остановится на
    ближайшем запросе к модели. Свои — любой пользователь; чужие и задачи расписания —
    администратор."""

    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    if job.created_by_id != user.id and user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Отменить чужой разбор может только администратор",
        )
    try:
        job = jobs.cancel(db, job, user)
    except jobs.JobNotCancellable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return BackgroundJobOut.model_validate(job, from_attributes=True)


def _require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Очереди других пользователей видит только администратор",
        )
