"""Профиль релевантности: просмотр групп и пересчёт (раздел 5.1.1 ТЗ).

Читать может любой пользователь: в карточке тендера видно, какая группа его отобрала, и без
доступа к списку групп это объяснение нечитаемо. Менять и пересчитывать — только
администратор: профиль определяет, что вообще попадает в систему.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import ai_relevance_service, relevance_service
from app.services.audit import log_action

router = APIRouter(prefix="/relevance", tags=["relevance"])


class KeywordGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    keywords: list[str]
    exclusion_keywords: list[str]
    okpd2_codes: list[str] | None
    search_queries: list[str]
    is_active: bool


class KeywordGroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    keywords: list[str] | None = None
    exclusion_keywords: list[str] | None = None
    okpd2_codes: list[str] | None = None
    search_queries: list[str] | None = None
    is_active: bool | None = None


class ProfileOut(BaseModel):
    groups: list[KeywordGroupOut]
    # Фразы, которыми система реально ходит в поиск площадок. Показываются вместе с
    # группами: именно они определяют, что вообще попадёт в базу, и расхождение между
    # ожиданием и этим списком — первое, что стоит проверять при жалобе «тендеров мало».
    search_queries: list[str]


class BackfillResultOut(BaseModel):
    processed: int
    passed: int
    rejected: int


@router.get("", response_model=ProfileOut)
def read_profile(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ProfileOut:
    relevance_service.get_or_create_profile(db)
    db.commit()
    return ProfileOut(
        groups=[KeywordGroupOut.model_validate(g) for g in relevance_service.active_groups(db)],
        search_queries=relevance_service.search_queries(db),
    )


@router.put("/groups/{group_id}", response_model=KeywordGroupOut)
def update_group(
    group_id: uuid.UUID,
    payload: KeywordGroupUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> KeywordGroupOut:
    group = db.get(relevance_service.SearchKeywordGroup, group_id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Группа ключевых слов не найдена"
        )
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field_name, value)
    log_action(
        db,
        component="relevance",
        action="update_keyword_group",
        result="ok",
        details=f"Группа «{group.name}»",
        user_id=admin.id,
    )
    db.commit()
    db.refresh(group)
    return KeywordGroupOut.model_validate(group)


@router.post("/recalculate", response_model=BackfillResultOut)
def recalculate(
    only_unprocessed: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> BackfillResultOut:
    """Пересчитывает отметки релевантности по всем собранным тендерам.

    Нужен после правки групп: без пересчёта список показывал бы отметки от прежних настроек.
    Выполняется синхронно — это один проход по таблице без обращений в сеть.
    """

    result = relevance_service.backfill(db, only_unprocessed=only_unprocessed)
    log_action(
        db,
        component="relevance",
        action="recalculate",
        result="ok",
        details=(
            f"Обработано {result['processed']}, прошли {result['passed']}, "
            f"отсеяно {result['rejected']}"
        ),
        user_id=admin.id,
    )
    db.commit()
    return BackfillResultOut(**result)


class AiCheckResultOut(BaseModel):
    checked: int
    relevant: int
    rejected: int
    failed: int
    messages: list[str]
    # Сколько ещё ждёт очереди: без этого числа кнопка «Проверить» выглядит бесконечной.
    pending: int


class AiPendingOut(BaseModel):
    pending: int


@router.get("/ai-pending", response_model=AiPendingOut)
def ai_pending(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AiPendingOut:
    return AiPendingOut(pending=ai_relevance_service.pending_count(db))


@router.post("/ai-check", response_model=AiCheckResultOut)
def ai_check(
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AiCheckResultOut:
    """ИИ-отбор: модель решает, действительно ли закупка наша (раздел 5.4 ТЗ).

    Пачками, а не всё разом: каждая проверка — вызов модели, и разбор нескольких тысяч
    накопленных закупок за один запрос упёрся бы в таймаут. Новые тендеры проверяются сами
    при сборе, эта кнопка нужна для накопленного архива и для повторного прогона.
    """

    result = ai_relevance_service.check_batch(db, limit=limit, actor=admin)
    return AiCheckResultOut(
        checked=result.checked,
        relevant=result.relevant,
        rejected=result.rejected,
        failed=result.failed,
        messages=result.messages,
        pending=ai_relevance_service.pending_count(db),
    )
