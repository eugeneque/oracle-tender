import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.jobs import enqueue_standalone, latest_standalone
from app.db.session import get_db
from app.models.job import JobKind
from app.models.source import POLL_EXCLUDED_SOURCE_TYPES, Source
from app.models.user import User
from app.schemas.job import BackgroundJobOut
from app.schemas.source import SourceOut, SourcePollResultOut, SourcesPollRequest
from app.services.tender_service import get_source_by_key, list_sources, poll_source

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
def get_sources(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[Source]:
    return list_sources(db)


@router.post("/poll", response_model=BackgroundJobOut, status_code=status.HTTP_202_ACCEPTED)
def post_poll_sources(
    payload: SourcesPollRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Опрос выбранного набора источников — кнопка «Синхронизировать» / модалка «Ресурсы»
    на странице тендеров. Доступно любому авторизованному пользователю (в отличие от
    поточечного `/sources/{id}/poll` в «Настройках», который остаётся действием администратора
    по разделу 5.6 ТЗ) — это рабочий сценарий "обновить список тендеров", а не управление
    источниками.

    Фоновой задачей, а не в запросе (баг 17.09.2026): опрос девяти площадок идёт 20–25
    минут, и запрос, открытый на всё это время, гас при любом перезапуске сервера или
    таймауте прокси — интерфейс крутил «Синхронизация…» вечно. Ответ — задача; её состояние
    и прогресс отдаёт `GET /sources/poll/current`. Неизвестные ключи и источники, которые
    опросом не охватываются (справочник продукции, ручные заявки), молча пропускаются."""

    keys = [
        key
        for key in dict.fromkeys(payload.source_keys)
        if (source := get_source_by_key(db, key)) is not None
        and source.type not in POLL_EXCLUDED_SOURCE_TYPES
    ]
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нет площадок для опроса — выберите источники в «Ресурсы»",
        )
    return enqueue_standalone(
        db, kind=JobKind.SOURCES_POLL, payload={"source_keys": keys}, actor=user
    )


@router.get("/poll/current", response_model=BackgroundJobOut | None)
def get_current_poll(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    """Последний опрос по кнопке — идущий или завершённый. Интерфейс опрашивает его, пока
    статус `queued`/`running`, и спрашивает при входе на страницу: запущенная до
    перезагрузки вкладки синхронизация должна быть видна."""

    return latest_standalone(db, JobKind.SOURCES_POLL)


@router.post("/{source_id}/poll", response_model=SourcePollResultOut)
def post_poll_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Источник не найден")
    return poll_source(db, source, actor_id=admin.id)
