"""Ключи доступа внешних систем к API (раздел 5.10 ТЗ — задел под интеграцию с Bitrix24).

Ключ выдаётся один раз при создании и больше не показывается: в базе лежит только его хеш
(тем же argon2, что и пароли пользователей) плюс короткий префикс для опознания в списке.

Поиск по префиксу, а не перебор всех ключей: хеш argon2 намеренно медленный, и проверять
каждый ключ в базе по очереди означало бы секунды на запрос при десятке интеграций.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.api_client import ApiClient
from app.models.log import LogLevel
from app.models.user import User
from app.services.audit import log_action

# Префикс в самом ключе — чтобы случайно попавший в лог или в переписку токен опознавался
# как ключ Sova Scanner, а не как случайная строка.
KEY_PREFIX = "orct_"
PREFIX_LENGTH = 12


def generate_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def create_client(db: Session, *, name: str, actor: User) -> tuple[ApiClient, str]:
    """Создаёт клиента и возвращает его вместе с ключом в открытом виде.

    Открытый ключ возвращается ровно здесь и никогда больше — вызывающий код обязан показать
    его администратору сразу.
    """

    key = generate_key()
    client = ApiClient(
        name=name.strip(),
        key_prefix=key[:PREFIX_LENGTH],
        key_hash=hash_password(key),
        created_by_id=actor.id,
    )
    db.add(client)
    log_action(
        db,
        component="integrations",
        action="create_api_client",
        result="success",
        level=LogLevel.INFO,
        details=f"Выпущен ключ доступа для «{client.name}»",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(client)
    return client, key


def list_clients(db: Session) -> list[ApiClient]:
    return list(db.scalars(select(ApiClient).order_by(ApiClient.created_at.desc())))


def revoke_client(db: Session, client_id: uuid.UUID, *, actor: User) -> ApiClient | None:
    """Отзывает ключ. Запись не удаляется: по журналу должно быть видно, что ключ
    существовал и когда им пользовались в последний раз."""

    client = db.get(ApiClient, client_id)
    if client is None:
        return None

    client.is_active = False
    log_action(
        db,
        component="integrations",
        action="revoke_api_client",
        result="success",
        level=LogLevel.WARNING,
        details=f"Отозван ключ доступа «{client.name}»",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(client)
    return client


def authenticate(db: Session, key: str | None) -> ApiClient | None:
    """Клиент по предъявленному ключу либо `None`."""

    if not key or not key.startswith(KEY_PREFIX):
        return None

    candidates = db.scalars(
        select(ApiClient).where(
            ApiClient.key_prefix == key[:PREFIX_LENGTH], ApiClient.is_active.is_(True)
        )
    ).all()

    for client in candidates:
        if verify_password(key, client.key_hash):
            client.last_used_at = datetime.now(timezone.utc)
            db.commit()
            return client
    return None
