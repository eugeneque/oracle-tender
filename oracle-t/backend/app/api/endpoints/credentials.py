"""Учётные данные площадок (раздел 4.1, 5.1 ТЗ) — раздел «Пользовательские данные» в
настройках.

Всё под ролью «Администратор»: пароли от личных кабинетов компании на ЭТП — не тот доступ,
который раздают всем пользователям системы. Пароль принимается на запись, но никогда не
отдаётся на чтение, поэтому GET безопасен для показа в интерфейсе.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.crypto import SecretStorageError
from app.db.session import get_db
from app.models.user import User
from app.schemas.source_credential import (
    SourceCredentialCreate,
    SourceCredentialOut,
    SourceCredentialUpdate,
)
from app.services import credentials_service
from app.services.credentials_service import CredentialError

router = APIRouter(prefix="/source-credentials", tags=["credentials"])


def _handle(exc: CredentialError) -> HTTPException:
    """«Не найдено» и «конфликт значений» приходят одним типом исключения из сервиса —
    различаем по тексту здесь, чтобы сервис не знал про коды HTTP."""

    code = (
        status.HTTP_404_NOT_FOUND
        if "не найден" in str(exc).lower()
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.get("", response_model=list[SourceCredentialOut])
def list_credentials(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
) -> list[SourceCredentialOut]:
    return credentials_service.list_credentials(db)


@router.post("", response_model=SourceCredentialOut, status_code=status.HTTP_201_CREATED)
def create_credential(
    payload: SourceCredentialCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> SourceCredentialOut:
    try:
        return credentials_service.create_credential(db, payload, actor=admin)
    except CredentialError as exc:
        raise _handle(exc) from exc
    except SecretStorageError as exc:
        # Недоступный файл ключа — это отказ хранилища секретов, а не ошибка запроса:
        # сохранять пароль в открытом виде «раз уж не получилось» нельзя ни при каких условиях.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.patch("/{credential_id}", response_model=SourceCredentialOut)
def update_credential(
    credential_id: uuid.UUID,
    payload: SourceCredentialUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> SourceCredentialOut:
    try:
        return credentials_service.update_credential(db, credential_id, payload, actor=admin)
    except CredentialError as exc:
        raise _handle(exc) from exc
    except SecretStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    credential_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> Response:
    try:
        credentials_service.delete_credential(db, credential_id, actor=admin)
    except CredentialError as exc:
        raise _handle(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
