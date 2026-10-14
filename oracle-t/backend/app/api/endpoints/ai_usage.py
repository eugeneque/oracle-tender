"""«Детализация ИИ» — расход токенов по моделям, шагам разбора и закупкам (14.10.2026).

Только администратору: по расходу видно, кто и что разбирал, а решения о модели и бюджете
принимает он. Логика — в `app/services/ai_usage_service.py`."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import ai_usage_service

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])

# Дольше года сводка не нужна, а запрос по всей таблице на годы вперёд стал бы тяжёлым.
MAX_PERIOD_DAYS = 366


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    default_from, default_to = ai_usage_service.default_period()
    start, end = date_from or default_from, date_to or default_to
    if start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Начало периода позже его конца",
        )
    if (end - start).days >= MAX_PERIOD_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Период детализации — не больше года",
        )
    return start, end


@router.get("/summary")
def get_usage_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    provider: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    """Сводка за период (по умолчанию — последние 30 дней по Москве)."""

    start, end = _period(date_from, date_to)
    return ai_usage_service.usage_summary(db, date_from=start, date_to=end, provider=provider or None)


@router.get("/users/{user_key}")
def get_user_usage(
    user_key: str,
    date_from: date | None = None,
    date_to: date | None = None,
    provider: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    """Расход одного пользователя: шаги разбора и закупки, которые он разбирал.
    `user_key` — id пользователя или `system`, как `key` в `by_user` сводки."""

    start, end = _period(date_from, date_to)
    try:
        return ai_usage_service.user_usage(
            db, user_key=user_key, date_from=start, date_to=end, provider=provider or None
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный пользователь"
        ) from exc


@router.get("/calls")
def get_usage_calls(
    date_from: date | None = None,
    date_to: date | None = None,
    provider: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    """Отдельные обращения к модели, новые сверху."""

    start, end = _period(date_from, date_to)
    return ai_usage_service.recent_calls(
        db, date_from=start, date_to=end, provider=provider or None, limit=limit, offset=offset
    )
