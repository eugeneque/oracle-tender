from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.log import LogLevel
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.integration_setting import AiProviderStatus, MyAiProviderUpdate
from app.schemas.user import MeOut, MeUpdate
from app.services import ai_provider_service
from app.services.ai_provider_service import AiNotConfiguredError
from app.services.audit import log_action
from app.services.user_service import (
    AvatarError,
    authenticate,
    clear_avatar,
    rename_self,
    set_avatar,
)

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


@router.patch("/me", response_model=MeOut)
def patch_me(
    payload: MeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Свои настройки учётной записи (замечание 17.09.2026): пользователь меняет имя.
    Логин и пароль здесь не меняются — это делает администратор в «Пользователях»."""

    return rename_self(db, current_user, payload.full_name)


@router.put("/me/ai-provider", response_model=AiProviderStatus)
def set_my_ai_provider(
    payload: MyAiProviderUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AiProviderStatus:
    """Личный выбор модели ИИ (18.09.2026): все запросы этого пользователя и запущенные им
    фоновые задачи идут через неё; другие пользователи не затрагиваются. `null` — вернуться
    к системной модели по умолчанию. На ненастроенную модель переключиться нельзя (400)."""

    try:
        return ai_provider_service.set_user_provider(db, current_user, payload.ai_provider)
    except AiNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/me/avatar", response_model=MeOut)
def put_my_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Загрузка аватара. Картинку уменьшает браузер до отправки; сервер проверяет тип и
    предел размера и хранит байты в строке пользователя."""

    try:
        return set_avatar(
            db,
            current_user,
            content=file.file.read(),
            content_type=file.content_type or "",
        )
    except AvatarError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.delete("/me/avatar", response_model=MeOut)
def delete_my_avatar(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> User:
    return clear_avatar(db, current_user)


@router.get("/me/avatar")
def get_my_avatar(current_user: User = Depends(get_current_user)) -> Response:
    """Байты своего аватара. Интерфейс забирает их fetch-ом с токеном и показывает через
    object URL: `<img src>` заголовок Authorization не отправляет."""

    if not current_user.avatar:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Аватар не загружен")
    return Response(
        content=current_user.avatar,
        media_type=current_user.avatar_content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )
