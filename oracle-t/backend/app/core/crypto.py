"""Симметричное шифрование секретов, хранимых в БД (учётные данные площадок, раздел 5.1 ТЗ).

Пароли к личным кабинетам ЭТП нельзя хранить в открытом виде: дамп базы или доступ к
`psql` не должен давать возможность войти на площадку от имени компании. Хешировать их, как
пароли пользователей, тоже нельзя — адаптеру нужен исходный пароль, чтобы подставить его в
форму входа. Поэтому обратимое шифрование: Fernet (AES-128-CBC + HMAC-SHA256) из
`cryptography`.

**Где живёт ключ.** В файле `<корень проекта>/.credentials_key`, правами 600, вне БД и вне
`.env`. Три причины именно так:

- ключ обязан пережить перезагрузку машины и обновление программы — иначе все сохранённые
  пароли превратятся в мусор, а по требованию заказчика они должны сохраняться;
- `.env` не подходит: `run.sh` при каждом запуске перезаписывает `backend/.env` копией
  корневого, и ключ, записанный не в тот файл, потерялся бы молча;
- ключ рядом с базой, а не в ней: секрет и шифротекст в одном дампе — это отсутствие
  шифрования.

Переменная окружения `CREDENTIALS_ENCRYPTION_KEY` имеет приоритет над файлом — так ключ
задаётся в контейнере/на сервере, где писать в файловую систему проекта не хочется.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

KEY_FILE_NAME = ".credentials_key"
ENV_VAR = "CREDENTIALS_ENCRYPTION_KEY"

# app/core/crypto.py → app/core → app → backend → корень проекта
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class SecretStorageError(RuntimeError):
    """Ключ шифрования недоступен или данные им не расшифровываются."""


def key_file_path() -> Path:
    return PROJECT_ROOT / KEY_FILE_NAME


def _read_or_create_key() -> bytes:
    env_key = os.environ.get(ENV_VAR)
    if env_key:
        return env_key.encode("utf-8")

    path = key_file_path()
    if path.exists():
        key = path.read_bytes().strip()
        if key:
            return key

    key = Fernet.generate_key()
    path.write_bytes(key + b"\n")
    # Ключ читаемый только владельцем: файл лежит в каталоге проекта, куда при разработке
    # заглядывают и редактор, и терминал.
    path.chmod(0o600)
    logger.warning(
        f"Создан новый ключ шифрования учётных данных: {path}. "
        "Не удаляйте этот файл — без него сохранённые пароли восстановить нельзя."
    )
    return key


@lru_cache
def _fernet() -> Fernet:
    try:
        return Fernet(_read_or_create_key())
    except (ValueError, OSError) as exc:
        raise SecretStorageError(
            f"Не удалось прочитать ключ шифрования ({key_file_path()}): {exc}"
        ) from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Расшифровывает значение. Подменённый или зашифрованный другим ключом текст даёт
    понятную ошибку, а не пустую строку: молчаливый возврат «ничего» привёл бы к попытке
    входа на площадку с пустым паролем и блокировке учётной записи."""

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretStorageError(
            "Не удалось расшифровать сохранённый пароль: файл ключа "
            f"{key_file_path()} заменён или утрачен. Введите пароль заново."
        ) from exc


def reset_key_cache() -> None:
    """Сбрасывает закэшированный ключ — нужен тестам, которые подменяют файл ключа."""

    _fernet.cache_clear()
