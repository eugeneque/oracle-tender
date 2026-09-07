"""Тесты учётных данных площадок (раздел 4.1, 5.1 ТЗ).

Главное, что здесь проверяется, — пароль не утекает: его нет ни в ответе API, ни в журнале,
ни в колонке БД в открытом виде. Плюс то, ради чего всё затевалось: пароль переживает
перезапуск программы (ключ читается с диска, а не живёт в памяти процесса) и не
расшифровывается чужим ключом.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core import crypto
from app.core.crypto import SecretStorageError, decrypt_secret, encrypt_secret
from app.models.log import Log
from app.models.source import Source
from app.models.source_credential import SourceCredential
from app.schemas.source_credential import SourceCredentialCreate, SourceCredentialUpdate
from app.services.credentials_service import (
    CredentialError,
    create_credential,
    delete_credential,
    get_credentials_for_source,
    list_credentials,
    update_credential,
)

SECRET = "Пароль от ЭТП 2026!"


def _source(db) -> Source:
    source = Source(
        key=f"cred_{uuid.uuid4().hex[:8]}",
        name="Площадка с авторизацией",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    return source


def test_encrypt_roundtrip_and_ciphertext_differs():
    token = encrypt_secret(SECRET)

    assert token != SECRET
    assert SECRET not in token
    assert decrypt_secret(token) == SECRET
    # Fernet подмешивает случайный вектор: два шифрования одного пароля дают разные токены,
    # поэтому по базе нельзя понять, что у двух площадок пароль совпадает.
    assert encrypt_secret(SECRET) != token


def test_key_survives_restart(tmp_path, monkeypatch):
    """Ключ живёт в файле, а не в памяти процесса: после «перезапуска» (сброса кэша) старый
    шифротекст обязан читаться — иначе все сохранённые пароли пропадали бы при перезагрузке."""

    monkeypatch.delenv(crypto.ENV_VAR, raising=False)
    monkeypatch.setattr(crypto, "PROJECT_ROOT", tmp_path)
    crypto.reset_key_cache()

    token = encrypt_secret(SECRET)
    assert crypto.key_file_path().exists()

    crypto.reset_key_cache()  # имитация перезапуска программы
    assert decrypt_secret(token) == SECRET

    crypto.reset_key_cache()


def test_foreign_key_cannot_decrypt(tmp_path, monkeypatch):
    monkeypatch.delenv(crypto.ENV_VAR, raising=False)
    monkeypatch.setattr(crypto, "PROJECT_ROOT", tmp_path)
    crypto.reset_key_cache()
    token = encrypt_secret(SECRET)

    # Файл ключа подменили (например, восстановили базу на другой машине) — пароль должен
    # дать понятную ошибку, а не пустую строку: вход с пустым паролем блокирует учётку.
    (tmp_path / crypto.KEY_FILE_NAME).unlink()
    crypto.reset_key_cache()
    with pytest.raises(SecretStorageError):
        decrypt_secret(token)

    crypto.reset_key_cache()


def test_password_is_encrypted_in_database(db_session, admin_user):
    source = _source(db_session)
    create_credential(
        db_session,
        SourceCredentialCreate(
            source_id=source.id, label="Основная", username="mirtek", password=SECRET
        ),
        actor=admin_user,
    )

    stored = db_session.scalar(
        select(SourceCredential).where(SourceCredential.source_id == source.id)
    )
    assert stored.password_encrypted != SECRET
    assert SECRET not in stored.password_encrypted
    assert decrypt_secret(stored.password_encrypted) == SECRET


def test_password_never_appears_in_output_or_log(db_session, admin_user):
    source = _source(db_session)
    created = create_credential(
        db_session,
        SourceCredentialCreate(
            source_id=source.id, label="Основная", username="mirtek", password=SECRET
        ),
        actor=admin_user,
    )

    assert SECRET not in created.model_dump_json()
    assert created.password_masked and SECRET not in created.password_masked

    entries = db_session.scalars(
        select(Log).where(Log.action == f"create_credential:{source.key}")
    ).all()
    assert entries, "действие с учёткой обязано попадать в журнал (раздел 5.9 ТЗ)"
    assert all(SECRET not in (entry.details or "") for entry in entries)


def test_update_keeps_password_when_not_sent(db_session, admin_user):
    """Переименование блока не должно требовать повторного ввода пароля."""

    source = _source(db_session)
    created = create_credential(
        db_session,
        SourceCredentialCreate(
            source_id=source.id, label="Основная", username="mirtek", password=SECRET
        ),
        actor=admin_user,
    )

    update_credential(
        db_session, created.id, SourceCredentialUpdate(label="Резервная"), actor=admin_user
    )

    resolved = get_credentials_for_source(db_session, source.key)
    assert [(item.label, item.password) for item in resolved] == [("Резервная", SECRET)]


def test_update_replaces_password(db_session, admin_user):
    source = _source(db_session)
    created = create_credential(
        db_session,
        SourceCredentialCreate(
            source_id=source.id, label="Основная", username="mirtek", password=SECRET
        ),
        actor=admin_user,
    )

    update_credential(
        db_session, created.id, SourceCredentialUpdate(password="новый-пароль"), actor=admin_user
    )

    assert get_credentials_for_source(db_session, source.key)[0].password == "новый-пароль"


def test_inactive_credentials_are_not_given_to_adapter(db_session, admin_user):
    """Выключенный блок — способ временно отозвать доступ, не удаляя запись; адаптер такой
    учёткой пользоваться не должен."""

    source = _source(db_session)
    create_credential(
        db_session,
        SourceCredentialCreate(
            source_id=source.id,
            label="Отключённая",
            username="old",
            password=SECRET,
            is_active=False,
        ),
        actor=admin_user,
    )

    assert get_credentials_for_source(db_session, source.key) == []


def test_duplicate_label_for_same_source_rejected(db_session, admin_user):
    source = _source(db_session)
    payload = SourceCredentialCreate(
        source_id=source.id, label="Основная", username="mirtek", password=SECRET
    )
    create_credential(db_session, payload, actor=admin_user)

    with pytest.raises(CredentialError):
        create_credential(db_session, payload, actor=admin_user)


def test_unknown_source_rejected(db_session, admin_user):
    with pytest.raises(CredentialError):
        create_credential(
            db_session,
            SourceCredentialCreate(
                source_id=uuid.uuid4(), label="Х", username="x", password=SECRET
            ),
            actor=admin_user,
        )


def test_delete_removes_credential(db_session, admin_user):
    source = _source(db_session)
    created = create_credential(
        db_session,
        SourceCredentialCreate(
            source_id=source.id, label="Основная", username="mirtek", password=SECRET
        ),
        actor=admin_user,
    )

    delete_credential(db_session, created.id, actor=admin_user)

    assert get_credentials_for_source(db_session, source.key) == []
    assert all(item.id != created.id for item in list_credentials(db_session))


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_api_crud_and_password_not_returned(client, admin_token):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        source = _source(db)
        db.commit()
        source_id = str(source.id)
    finally:
        db.close()

    created = client.post(
        "/source-credentials",
        json={
            "source_id": source_id,
            "label": "Основная учётка",
            "username": "mirtek",
            "password": SECRET,
            "notes": "закрытый раздел",
        },
        headers=_headers(admin_token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "password" not in body
    assert SECRET not in created.text
    assert body["source_key"]

    listed = client.get("/source-credentials", headers=_headers(admin_token))
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert any(item["id"] == body["id"] for item in listed.json())

    patched = client.patch(
        f"/source-credentials/{body['id']}",
        json={"is_active": False},
        headers=_headers(admin_token),
    )
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    deleted = client.delete(f"/source-credentials/{body['id']}", headers=_headers(admin_token))
    assert deleted.status_code == 204

    missing = client.patch(
        f"/source-credentials/{uuid.uuid4()}", json={"label": "x"}, headers=_headers(admin_token)
    )
    assert missing.status_code == 404


def test_api_requires_admin(client, admin_token):
    """Пароли от кабинетов компании видит только администратор (раздел 3 ТЗ)."""

    created = client.post(
        "/users",
        json={
            "username": f"viewer_{uuid.uuid4().hex[:6]}",
            "password": "ViewerPass123!",
            "full_name": "Наблюдатель",
            "role": "user",
        },
        headers=_headers(admin_token),
    )
    assert created.status_code in (200, 201), created.text

    token = client.post(
        "/auth/login",
        json={"username": created.json()["username"], "password": "ViewerPass123!"},
    ).json()["access_token"]

    assert client.get("/source-credentials", headers=_headers(token)).status_code == 403
    assert client.get("/source-credentials").status_code == 401
