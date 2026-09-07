"""Учётные данные площадок: хранение, выдача интерфейсу и выдача адаптерам
(раздел 4.1, 5.1, 5.9 ТЗ).

Два разных потребителя и намеренно разные функции для них:

- интерфейс получает `SourceCredentialOut` — без пароля вообще, только маска;
- адаптер получает `ResolvedCredential` с расшифрованным паролем, и только по ключу
  источника, для которого он написан.

Единственное место, где пароль расшифровывается, — `get_credentials_for_source`. Пароль
никогда не попадает ни в журнал (`log_action` получает только логин и метку), ни в текст
ошибок, ни в ответ API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.log import LogLevel
from app.models.source import Source
from app.models.source_credential import SourceCredential
from app.models.user import User
from app.schemas.source_credential import (
    SourceCredentialCreate,
    SourceCredentialOut,
    SourceCredentialUpdate,
)
from app.services.audit import log_action

# Сколько точек показывать вместо пароля. Не длина настоящего пароля: она сама по себе
# подсказка для подбора, а пользы человеку не несёт — ему важно лишь, задан пароль или нет.
MASK = "•" * 8


class CredentialError(ValueError):
    """Некорректный запрос на изменение учётки — эндпоинт превращает в 4xx."""


@dataclass(frozen=True)
class ResolvedCredential:
    """Учётка с расшифрованным паролем — для адаптера, не для API."""

    label: str
    username: str
    password: str


def _to_out(db: Session, credential: SourceCredential) -> SourceCredentialOut:
    updated_by = db.get(User, credential.updated_by_id) if credential.updated_by_id else None
    return SourceCredentialOut(
        id=credential.id,
        source_id=credential.source_id,
        source_key=credential.source.key,
        source_name=credential.source.name,
        label=credential.label,
        username=credential.username,
        password_masked=MASK,
        notes=credential.notes,
        is_active=credential.is_active,
        updated_at=credential.updated_at,
        updated_by=updated_by.full_name if updated_by else None,
    )


def list_credentials(db: Session) -> list[SourceCredentialOut]:
    credentials = db.scalars(
        select(SourceCredential).join(Source).order_by(Source.name, SourceCredential.label)
    ).all()
    return [_to_out(db, credential) for credential in credentials]


def _get_or_error(db: Session, credential_id: uuid.UUID) -> SourceCredential:
    credential = db.get(SourceCredential, credential_id)
    if credential is None:
        raise CredentialError("Учётные данные не найдены")
    return credential


def create_credential(
    db: Session, payload: SourceCredentialCreate, *, actor: User
) -> SourceCredentialOut:
    source = db.get(Source, payload.source_id)
    if source is None:
        raise CredentialError("Источник не найден")

    credential = SourceCredential(
        source_id=source.id,
        label=payload.label.strip(),
        username=payload.username.strip(),
        password_encrypted=encrypt_secret(payload.password),
        notes=payload.notes,
        is_active=payload.is_active,
        updated_by_id=actor.id,
    )
    db.add(credential)
    # Запись сбрасывается в БД до журналирования: уникальность (source_id, label)
    # проверяет Postgres, и без явного flush конфликт всплыл бы позже — из `log_action`,
    # мимо обработчика ниже.
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise CredentialError(
            f"Для площадки «{source.name}» уже есть блок с названием «{payload.label}»"
        ) from exc

    log_action(
        db,
        component="credentials",
        action=f"create_credential:{source.key}",
        result="success",
        level=LogLevel.INFO,
        details=f"Блок «{credential.label}», логин {credential.username}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(credential)
    return _to_out(db, credential)


def update_credential(
    db: Session, credential_id: uuid.UUID, payload: SourceCredentialUpdate, *, actor: User
) -> SourceCredentialOut:
    credential = _get_or_error(db, credential_id)
    fields_set = payload.model_fields_set

    if "label" in fields_set and payload.label is not None:
        credential.label = payload.label.strip()
    if "username" in fields_set and payload.username is not None:
        credential.username = payload.username.strip()
    if "password" in fields_set and payload.password is not None:
        credential.password_encrypted = encrypt_secret(payload.password)
    if "notes" in fields_set:
        credential.notes = payload.notes
    if "is_active" in fields_set and payload.is_active is not None:
        credential.is_active = payload.is_active
    credential.updated_by_id = actor.id

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise CredentialError("Блок с таким названием для этой площадки уже существует") from exc

    # В журнал — перечень изменённых полей, но никогда само значение пароля.
    changed = sorted(fields_set)
    log_action(
        db,
        component="credentials",
        action=f"update_credential:{credential.source.key}",
        result="success",
        level=LogLevel.INFO,
        details=f"Блок «{credential.label}», изменены поля: {', '.join(changed) or '(нет)'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(credential)
    return _to_out(db, credential)


def delete_credential(db: Session, credential_id: uuid.UUID, *, actor: User) -> None:
    credential = _get_or_error(db, credential_id)
    log_action(
        db,
        component="credentials",
        action=f"delete_credential:{credential.source.key}",
        result="success",
        level=LogLevel.INFO,
        details=f"Блок «{credential.label}», логин {credential.username}",
        user_id=actor.id,
    )
    db.delete(credential)
    db.commit()


def get_credentials_for_source(db: Session, source_key: str) -> list[ResolvedCredential]:
    """Активные учётки площадки с расшифрованными паролями — для адаптера.

    Возвращает список, а не одну запись: у площадки бывает учётка на юрлицо, и адаптер сам
    решает, какой войти (или перебрать по очереди при блокировке одной из них)."""

    credentials = db.scalars(
        select(SourceCredential)
        .join(Source)
        .where(Source.key == source_key, SourceCredential.is_active.is_(True))
        .order_by(SourceCredential.label)
    ).all()
    return [
        ResolvedCredential(
            label=credential.label,
            username=credential.username,
            password=decrypt_secret(credential.password_encrypted),
        )
        for credential in credentials
    ]
