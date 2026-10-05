import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.user_service import get_user_by_id

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверные или отсутствующие учётные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized

    user = get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_role(*allowed_roles: UserRole):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in {role.value for role in allowed_roles}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для выполнения действия",
            )
        return current_user

    return dependency


require_admin = require_role(UserRole.ADMIN)


def parse_okpd2(values: list[str] | None) -> list[str]:
    """Префиксы ОКПД2 из query: некорректный код — 422, а не молча пустая выдача."""

    from app.services import okpd2_service

    try:
        return okpd2_service.clean_prefixes(values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def resolve_relevance_profiles(db, profile_ids, *, include_defaults: bool = False):
    """Профили для фильтра списка: досчитывает совпадения и собирает площадки каждого.

    Нет профиля — 404 с пояснением: молча отдать «ничего не подошло» значило бы показать
    пустой список вместо объяснения, что профиль удалили.
    """

    from sqlalchemy import select

    profile_ids = list(profile_ids or [])
    if include_defaults:
        # «Общие профили по умолчанию» — то, что раньше было галочкой «Только прошедшие
        # профиль»: действующие общие профили, тем же механизмом, что и выбранные вручную.
        from app.services import relevance_service

        profile_ids = [p.id for p in relevance_service.default_profiles(db)] + profile_ids
    if not profile_ids:
        return []

    from app.models.source import Source
    from app.services import relevance_profile_service
    from app.services.tender_service import ProfileScope

    result = []
    for profile_id in dict.fromkeys(profile_ids):
        profile = relevance_profile_service.prepare_for_filter(db, profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Профиль релевантности не найден — возможно, его удалили",
            )
        source_ids = None
        if profile.source_keys:
            source_ids = tuple(
                db.scalars(select(Source.id).where(Source.key.in_(profile.source_keys)))
            )
        result.append(ProfileScope(profile_id=profile.id, source_ids=source_ids))
    return result
