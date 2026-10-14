import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("POSTGRES_DB", "oraclet_test")
# Тесты создают и удаляют данные — только в отдельной базе с суффиксом `_test`. 30.09.2026
# прогон с выставленным в терминале POSTGRES_DB=oraclet записал в рабочую базу 13 источников
# «Тестовая площадка»/«Тестовый источник» с закупками — они всплыли в выборе площадок.
if not os.environ["POSTGRES_DB"].endswith("_test"):
    raise pytest.UsageError(
        f"POSTGRES_DB={os.environ['POSTGRES_DB']}: тесты запускаются только на базе *_test "
        "(по умолчанию oraclet_test). Снимите переменную: unset POSTGRES_DB"
    )
os.environ.setdefault("BOOTSTRAP_ADMIN_USERNAME", "admin")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "TestAdmin123!")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("JOBS_RESUME_INTERRUPTED", "false")

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_database() -> None:
    """Каждый прогон начинается с чистой базы: схема пересоздаётся и накатывается миграциями.

    14.10.2026: `db_session` откатывает свои изменения, но HTTP-тесты (`client`) коммитят
    напрямую — за прошлые прогоны в oraclet_test набралось 4466 производителей, 36797 закупок,
    6505 источников, 2568 пользователей. `evaluate_tender` перебирает всех производителей базы,
    и test_compliance_service.py шёл ~7 минут вместо секунд. Пересоздание схемы (а не TRUNCATE)
    сохраняет заготовки, которые кладут сами миграции (общие профили отбора и т. п.).
    Защита от рабочей базы — проверка суффикса `_test` в начале файла и повторно здесь."""

    from sqlalchemy import create_engine, text

    from app.core.config import get_settings

    settings = get_settings()
    assert settings.postgres_db.endswith("_test"), settings.postgres_db
    engine = create_engine(settings.database_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    # `sys.executable -m alembic`, а не `alembic`: без активированного venv команды нет в PATH.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True)


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
