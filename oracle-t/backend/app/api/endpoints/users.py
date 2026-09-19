import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services.user_service import (
    create_user,
    get_user_by_id,
    list_users,
    set_user_active,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def get_users(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> list[User]:
    return list_users(db)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def post_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    try:
        return create_user(db, payload, admin)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким логином уже существует",
        )


def _get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return user


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    user = _get_user_or_404(db, user_id)
    return update_user(db, user, payload, admin)


@router.post("/{user_id}/block", response_model=UserOut)
def block_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя заблокировать самого себя",
        )
    user = _get_user_or_404(db, user_id)
    return set_user_active(db, user, is_active=False, actor=admin)


@router.post("/{user_id}/unblock", response_model=UserOut)
def unblock_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> User:
    user = _get_user_or_404(db, user_id)
    return set_user_active(db, user, is_active=True, actor=admin)


@router.get("/{user_id}/avatar")
def get_user_avatar(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Response:
    """Аватар любого пользователя — для списка пользователей и подписей «кто поставил».
    Доступен всем вошедшим: аватар — публичное лицо в системе, а не личные данные."""

    user = _get_user_or_404(db, user_id)
    if not user.avatar:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Аватар не загружен")
    return Response(
        content=user.avatar,
        media_type=user.avatar_content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )
