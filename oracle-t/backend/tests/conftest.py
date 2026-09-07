import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("POSTGRES_DB", "oraclet_test")
os.environ.setdefault("BOOTSTRAP_ADMIN_USERNAME", "admin")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "TestAdmin123!")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_database() -> None:
    subprocess.run(["alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_token(client) -> str:
    response = client.post(
        "/auth/login",
        json={
            "username": os.environ["BOOTSTRAP_ADMIN_USERNAME"],
            "password": os.environ["BOOTSTRAP_ADMIN_PASSWORD"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def db_session():
    """Сессия к тестовой БД для тестов сервисного слоя (без HTTP).

    Сессия живёт во внешней транзакции, которая откатывается после теста. Это нужно потому,
    что сервисы этапов 5-6 коммитят сами (расчёт обязан переживать сбой на очередном
    производителе), и без отката тестовые тендеры и производители накапливались бы в общей
    базе — соседние тесты, проверяющие содержимое справочников, начинали бы падать.
    `join_transaction_mode="create_savepoint"` превращает внутренние `commit()` в release
    savepoint, оставляя внешнюю транзакцию открытой."""

    from app.db.session import SessionLocal, engine

    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def admin_user(db_session):
    """Пользователь-администратор, созданный бутстрапом приложения, — как автор действий
    в журнале (`log_action` требует существующего пользователя)."""

    from sqlalchemy import select

    from app.models.user import User

    user = db_session.scalar(
        select(User).where(User.username == os.environ["BOOTSTRAP_ADMIN_USERNAME"])
    )
    assert user is not None, "администратор не создан бутстрапом — проверьте фикстуру client"
    return user
