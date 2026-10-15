"""Массовый пересчёт матрицы соответствия — только администратору (15.10.2026).

Пересчёт расходует токены ИИ на каждую закупку, поэтому сначала — оценка (`GET /estimate`:
сколько закупок, токенов, денег и времени по фактическому расходу), затем запуск
(`POST /`). Логика — в `app/services/matrix_recalc_service.py`."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import matrix_recalc_service
from app.services.matrix_recalc_service import RecalcError

router = APIRouter(prefix="/matrix-recalc", tags=["matrix-recalc"])


class RecalcIn(BaseModel):
    mode: str
    scope: str


@router.get("/estimate")
def get_estimate(
    mode: str = Query("evaluation"),
    scope: str = Query("open"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    try:
        return matrix_recalc_service.estimate(db, mode, scope)
    except RecalcError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.post("")
def start_recalc(
    body: RecalcIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    try:
        return matrix_recalc_service.launch(db, body.mode, body.scope, user)
    except RecalcError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
