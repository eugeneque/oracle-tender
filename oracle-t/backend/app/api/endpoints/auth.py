from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.log import LogLevel
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import MeOut
from app.services.audit import log_action
from app.services.user_service import authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate(db, payload.username, payload.password)
    if user is None:
        log_action(
            db,
            component="auth",
            action=f"login:{payload.username}",
            result="failure",
            level=LogLevel.WARNING,
            details="Неверный логин/пароль либо учётная запись заблокирована",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    log_action(
        db,
        component="auth",
        action=f"login:{user.username}",
        result="success",
        user_id=user.id,
    )
    db.commit()

    token = create_access_token(subject=str(user.id), extra_claims={"role": user.role})
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, str]:
    log_action(
        db,
        component="auth",
        action=f"logout:{current_user.username}",
        result="success",
        user_id=current_user.id,
    )
    db.commit()
    return {"detail": "Выход выполнен"}


@router.get("/me", response_model=MeOut)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
