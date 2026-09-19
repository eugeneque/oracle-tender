"""Настройки подключения к Yandex AI Studio (раздел 5.4 ТЗ) — хранятся в БД и управляются со
страницы «Интеграции» в интерфейсе (роль «Администратор»), а не в `.env`: заказчик не хочет заходить в
код/конфигурацию сервера каждый раз, когда нужно обновить ключ. Таблица `yandex_ai_studio_settings`
— синглтон, всегда ровно одна строка (`_SINGLETON_ID`)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.integration_setting import YandexAiStudioSettings
from app.models.log import LogLevel
from app.models.user import User
from app.schemas.integration_setting import (
    YandexAiStudioSettingsOut,
    YandexAiStudioSettingsUpdate,
    YandexConnectionTestResult,
)
from app.services.audit import log_action

_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_UNCONFIGURED_MESSAGE = (
    "Подключение не настроено: заполните API-ключ и Folder ID на странице «Интеграции»."
)


def _get_or_create(db: Session) -> YandexAiStudioSettings:
    settings = db.get(YandexAiStudioSettings, _SINGLETON_ID)
    if settings is None:
        settings = YandexAiStudioSettings(id=_SINGLETON_ID)
        db.add(settings)
        db.flush()
    return settings


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return f"{'•' * 8}{api_key[-4:]}"


def to_out(db: Session, settings: YandexAiStudioSettings) -> YandexAiStudioSettingsOut:
    updated_by_user = db.get(User, settings.updated_by_id) if settings.updated_by_id else None
    updated_by_name = updated_by_user.full_name if updated_by_user else None
    return YandexAiStudioSettingsOut(
        is_configured=bool(settings.api_key and settings.folder_id),
        api_key_masked=mask_api_key(settings.api_key) if settings.api_key else None,
        folder_id=settings.folder_id,
        updated_at=settings.updated_at if (settings.api_key or settings.folder_id) else None,
        updated_by=updated_by_name,
    )


def get_settings_out(db: Session) -> YandexAiStudioSettingsOut:
    return to_out(db, _get_or_create(db))


def update_settings(
    db: Session, payload: YandexAiStudioSettingsUpdate, *, actor: User
) -> YandexAiStudioSettingsOut:
    """PATCH-семантика: поле, не присланное в теле запроса, не трогаем (например, фронтенд
    отправляет только `folder_id`, оставляя ранее сохранённый `api_key` как есть — иначе
    пришлось бы каждый раз вводить ключ заново, чтобы поменять только Folder ID). Пришедшее
    явно пустой строкой — сознательная очистка значения."""

    settings = _get_or_create(db)
    fields_set = payload.model_fields_set

    if "api_key" in fields_set:
        settings.api_key = payload.api_key or None
    if "folder_id" in fields_set:
        settings.folder_id = payload.folder_id or None
    settings.updated_by_id = actor.id

    log_action(
        db,
        component="integrations",
        action="update_yandex_ai_studio_settings",
        result="success",
        level=LogLevel.INFO,
        details=f"Изменены поля: {', '.join(sorted(fields_set)) or '(нет изменений)'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(settings)
    return to_out(db, settings)


def test_connection(db: Session, *, actor: User) -> YandexConnectionTestResult:
    """Реальный вызов Yandex Vision OCR на 1x1-пикселе — самый дешёвый способ проверить, что
    API-ключ и Folder ID действительно валидны и площадка доступна, не тратя токены на
    текстовую генерацию. Ошибка любого рода (нет ключа, неверный Folder ID, сетевой сбой) не
    поднимается наружу как 500 — это ожидаемый результат проверки, а не баг API."""

    settings = _get_or_create(db)
    if not settings.api_key or not settings.folder_id:
        result = YandexConnectionTestResult(success=False, message=_UNCONFIGURED_MESSAGE)
    else:
        result = _run_ocr_ping(settings.api_key, settings.folder_id)

    log_action(
        db,
        component="integrations",
        action="test_yandex_ai_studio_connection",
        result="success" if result.success else "error",
        level=LogLevel.INFO if result.success else LogLevel.WARNING,
        details=result.message,
        user_id=actor.id,
    )
    db.commit()
    return result


# Пустой белый PNG 32×32 — минимальный образ для пинга Vision OCR (не тратит лишний трафик/
# токены на реальный документ). Байты сгенерированы через Pillow и проверены round-trip'ом
# (тест `test_probe_png_is_a_valid_decodable_image`) — руками собранный 1×1 PNG однажды уже
# оказался повреждён (не тот CRC/IDAT) и OCR отвечал `INVALID_ARGUMENT: Can't decode image`,
# что выглядело как проблема с ключом/Folder ID, хотя ей не являлось.
_PROBE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000020000000200802000000fc18"
    "eda30000003749444154789cedd1c10d003008c3c094fd774e47301f7ebe01"
    "82645edb5c9ad3f57860c11f20132113211321132113211321132113857cc7"
    "f1033d38c33a7c0000000049454e44ae426082"
)


def _run_ocr_ping(api_key: str, folder_id: str) -> YandexConnectionTestResult:
    try:
        from yandex_ai_studio_sdk import AIStudio
    except ImportError:
        return YandexConnectionTestResult(
            success=False, message="Пакет yandex-ai-studio-sdk не установлен на сервере."
        )

    try:
        sdk = AIStudio(folder_id=folder_id, auth=api_key)
        ocr = sdk.vision.ocr(language_codes=["ru", "en"], model="page")
        ocr.run(_PROBE_PNG)
    except Exception as exc:  # noqa: BLE001 - любая ошибка подключения - ожидаемый исход теста, не баг
        return YandexConnectionTestResult(
            success=False, message=f"Не удалось подключиться: {exc}"
        )

    return YandexConnectionTestResult(success=True, message="Подключение работает.")
