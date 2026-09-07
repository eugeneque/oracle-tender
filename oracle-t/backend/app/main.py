from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.router import api_router
from app.core.config import get_settings
from app.core.jobs import recover_interrupted_jobs, shutdown as shutdown_jobs
from app.core.logging import configure_logging
from app.core.scheduler import start_scheduler, stop_scheduler
from app.db.session import SessionLocal
from app.middleware.audit import AuditLogMiddleware
# Импорт регистрирует обработчики фоновых задач в очереди (см. app/core/jobs.py).
from app.services import job_runner  # noqa: F401
from app.services import catalog_queue_service, catalog_site_sync, fgis_catalog_sync
from app.services.notification_service import bootstrap_from_env as bootstrap_notifications
from app.services.user_service import bootstrap_admin

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db = SessionLocal()
    try:
        admin = bootstrap_admin(
            db,
            username=settings.bootstrap_admin_username,
            password=settings.bootstrap_admin_password,
            full_name=settings.bootstrap_admin_full_name,
        )
        if admin is not None:
            logger.info(f"Создан первый администратор: {admin.username}")
        bootstrap_notifications(db)
    finally:
        db.close()
    recover_interrupted_jobs()
    # Исполнители очереди справочника продукции регистрируются до старта планировщика:
    # плановая ревалидация ФГИС и обход каталога МИРТЕК идут через эту же очередь.
    fgis_catalog_sync.register()
    catalog_site_sync.register()
    catalog_queue_service.recover_interrupted_tasks()
    start_scheduler()
    yield
    stop_scheduler()
    shutdown_jobs()
    catalog_queue_service.shutdown()


app = FastAPI(title="ORACLE-T API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditLogMiddleware)

app.include_router(api_router)
