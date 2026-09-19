"""Справочник тегов закупок (замечание 17.09.2026). Набор тегов конкретной закупки —
`PUT /tenders/{id}/tags` рядом с избранным."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tender_tag import TAG_COLORS
from app.models.user import User
from app.schemas.tender import TenderTagIn, TenderTagOut
from app.services import tag_service
from app.services.tag_service import TagError

router = APIRouter(prefix="/tags", tags=["tags"])


def _get_tag_or_404(db: Session, tag_id: uuid.UUID):
    tag = tag_service.get_tag(db, tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тег не найден")
    return tag


@router.get("", response_model=list[TenderTagOut])
def get_tags(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return tag_service.list_tags(db)


@router.get("/colors", response_model=list[str])
def get_tag_colors(_user: User = Depends(get_current_user)) -> list[str]:
    """Допустимые ключи палитры — чтобы интерфейс и сервер не разошлись в списке цветов."""

    return list(TAG_COLORS)


@router.post("", response_model=TenderTagOut, status_code=status.HTTP_201_CREATED)
def post_tag(
    payload: TenderTagIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return tag_service.create_tag(db, name=payload.name, color=payload.color, actor=user)
    except TagError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/{tag_id}", response_model=TenderTagOut)
def patch_tag(
    tag_id: uuid.UUID,
    payload: TenderTagIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    tag = _get_tag_or_404(db, tag_id)
    try:
        return tag_service.update_tag(db, tag, name=payload.name, color=payload.color)
    except TagError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_tag(
    tag_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    tag_service.delete_tag(db, _get_tag_or_404(db, tag_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
