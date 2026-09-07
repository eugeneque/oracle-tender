"""Тесты почтовых уведомлений (раздел 5.8 ТЗ) и их журнала.

Настоящий SMTP не дёргается: проверяется решение «отправлять или нет» и то, что попадает в
журнал. Именно здесь ломается тихо и дорого — письмо, ушедшее дважды, письмо, не ушедшее
вовсе, и пароль ящика, случайно оказавшийся в ответе API.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.crypto import decrypt_secret
from app.models.ai_profile import AiProfileScore, Verdict
from app.models.notification import Notification, NotificationStatus, NotificationTrigger
from app.models.source import Source
from app.models.tender import Tender
from app.schemas.notification import NotificationSettingsUpdate
from app.services import notification_service


def _tender(db, **overrides) -> Tender:
    source = Source(
        key=f"notify_{uuid.uuid4().hex[:8]}",
        name="Площадка для теста уведомлений",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    fields = {
        "title": "Поставка приборов учёта электроэнергии",
        "currency": "RUB",
        # 26.51.63.130 — приоритетный код раздела 5.4 ТЗ, тендер с ним релевантен.
        "okpd2_code": "26.51.63.130",
        **overrides,
    }
    tender = Tender(source_id=source.id, external_id=f"NTF-{uuid.uuid4().hex[:6]}", **fields)
    db.add(tender)
    db.flush()
    return tender


@pytest.fixture()
def configured_mail(db_session, monkeypatch):
    """Канал «настроен и включён», но реальная отправка подменена: тестам нужен вердикт
    сервиса, а не живой почтовый сервер."""

    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = True
    settings.smtp_host = "smtp.example.test"
    settings.from_address = "oracle-t@example.test"
    settings.recipients = "sales@example.test"
    settings.admin_recipients = "admin@example.test"
    db_session.flush()

    sent: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        notification_service,
        "_send_email",
        lambda cfg, recipients, subject, body, html=None: sent.append((recipients, subject)),
    )
    return sent


def test_new_relevant_tender_is_sent_once(db_session, configured_mail):
    """Повторный анализ того же тендера не должен слать второе письмо: анализ перезапускают
    при обновлении документации, и дубли быстро приучают не читать рассылку."""

    tender = _tender(db_session)

    first = notification_service.notify_new_relevant_tender(db_session, tender)
    second = notification_service.notify_new_relevant_tender(db_session, tender)

    assert first is not None and first.status == NotificationStatus.SENT.value
    assert second is None
    assert len(configured_mail) == 1


def test_irrelevant_okpd2_is_not_notified(db_session, configured_mail):
    tender = _tender(db_session, okpd2_code="43.21.10")

    assert notification_service.notify_new_relevant_tender(db_session, tender) is None
    assert configured_mail == []


def test_unconfigured_channel_still_records_the_event(db_session, monkeypatch):
    """Пока почта не настроена, событие пишется со статусом `skipped` — иначе после
    включения канала нельзя проверить, что триггеры вообще срабатывали."""

    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = False
    settings.smtp_host = None
    settings.recipients = None
    db_session.flush()

    tender = _tender(db_session)
    entry = notification_service.notify_new_relevant_tender(db_session, tender)

    assert entry is not None
    assert entry.status == NotificationStatus.SKIPPED.value
    assert "выключены" in (entry.error or "")


def test_failed_send_is_recorded_but_does_not_raise(db_session, monkeypatch):
    """Сбой почтового сервера не должен ронять операцию, из которой уведомление вызвано
    (раздел 5.9 ТЗ): анализ тендера важнее письма о нём."""

    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = True
    settings.smtp_host = "smtp.example.test"
    settings.from_address = "oracle-t@example.test"
    settings.recipients = "sales@example.test"
    db_session.flush()

    def _explode(*args, **kwargs):
        raise RuntimeError("SMTP-сервер недоступен")

    monkeypatch.setattr(notification_service, "_send_email", _explode)

    tender = _tender(db_session)
    entry = notification_service.notify_new_relevant_tender(db_session, tender)

    assert entry is not None
    assert entry.status == NotificationStatus.FAILED.value
    assert "SMTP-сервер недоступен" in (entry.error or "")


def test_high_ai_score_respects_threshold(db_session, configured_mail):
    """Порог берётся от итоговой AI-оценки по профилю, а не от процента победителя
    (раздел 5.8 ТЗ, решение 03.09.2026)."""

    settings = notification_service.get_or_create(db_session)
    settings.ai_score_threshold = 80
    db_session.flush()

    low = _tender(db_session)
    db_session.add(
        AiProfileScore(
            tender_id=low.id,
            task_score=Decimal("60.00"),
            competencies_score=Decimal("64.00"),
            overall_score=Decimal("62.00"),
            verdict=Verdict.GO_WITH_RESERVATIONS.value,
        )
    )
    high = _tender(db_session)
    db_session.add(
        AiProfileScore(
            tender_id=high.id,
            task_score=Decimal("90.00"),
            competencies_score=Decimal("93.00"),
            overall_score=Decimal("91.50"),
            verdict=Verdict.GO.value,
        )
    )
    db_session.flush()

    assert notification_service.notify_high_ai_score(db_session, low) is None
    entry = notification_service.notify_high_ai_score(db_session, high)

    assert entry is not None and entry.status == NotificationStatus.SENT.value
    # 91.5 в теме округляется до целого процента.
    assert "92" in entry.subject
    # История без данных не превращается в ноль — в письме так и написано (раздел 5.5.1 ТЗ).
    assert "История: —" in (entry.body or "")


def test_deadline_trigger_picks_only_tenders_inside_horizon(db_session, configured_mail):
    settings = notification_service.get_or_create(db_session)
    settings.deadline_days_threshold = 5
    db_session.flush()

    now = datetime.now(timezone.utc)
    soon = _tender(db_session, application_end=now + timedelta(days=3))
    later = _tender(db_session, application_end=now + timedelta(days=20))
    expired = _tender(db_session, application_end=now - timedelta(days=1))

    sent = notification_service.notify_deadlines_soon(db_session)
    notified_ids = {entry.tender_id for entry in sent}

    assert soon.id in notified_ids
    assert later.id not in notified_ids
    assert expired.id not in notified_ids


def test_critical_error_is_not_repeated_within_cooldown(db_session, configured_mail):
    """Упавший источник роняет пачку задач подряд — админ не должен получить двадцать
    одинаковых писем за минуту."""

    first = notification_service.notify_critical_error(
        db_session, subject="Опрос источника «ЕИС» завершился ошибкой", details="таймаут"
    )
    second = notification_service.notify_critical_error(
        db_session, subject="Опрос источника «ЕИС» завершился ошибкой", details="таймаут"
    )

    assert first is not None and first.status == NotificationStatus.SENT.value
    assert second is None


def test_critical_error_goes_to_admin_recipients(db_session, configured_mail):
    notification_service.notify_critical_error(
        db_session, subject=f"Сбой {uuid.uuid4().hex[:6]}", details="подробности"
    )

    recipients, _subject = configured_mail[-1]
    assert recipients == ["admin@example.test"]


def test_disabled_trigger_sends_nothing(db_session, configured_mail):
    settings = notification_service.get_or_create(db_session)
    settings.trigger_new_relevant = False
    db_session.flush()

    tender = _tender(db_session)
    assert notification_service.notify_new_relevant_tender(db_session, tender) is None
    assert configured_mail == []


def test_recipients_are_parsed_from_free_form_text():
    raw = "one@example.test, two@example.test; three@example.test\nfour@example.test"
    assert notification_service.parse_recipients(raw) == [
        "one@example.test",
        "two@example.test",
        "three@example.test",
        "four@example.test",
    ]


def test_password_is_encrypted_and_never_returned(db_session, admin_user):
    """Пароль почтового ящика хранится зашифрованным (тот же ключ, что у паролей площадок)
    и наружу отдаётся только признаком «задан»."""

    out = notification_service.update_settings(
        db_session,
        NotificationSettingsUpdate(
            smtp_host="smtp.example.test",
            smtp_username="robot@example.test",
            smtp_password="СекретноеСлово123",
            from_address="robot@example.test",
            recipients="sales@example.test",
        ),
        actor=admin_user,
    )

    assert out.has_password is True
    assert "СекретноеСлово123" not in out.model_dump_json()

    stored = notification_service.get_or_create(db_session)
    assert stored.smtp_password_encrypted is not None
    assert "СекретноеСлово123" not in stored.smtp_password_encrypted


def test_settings_patch_keeps_password_when_not_sent(db_session, admin_user):
    notification_service.update_settings(
        db_session,
        NotificationSettingsUpdate(smtp_password="ПервыйПароль"),
        actor=admin_user,
    )
    stored_before = notification_service.get_or_create(db_session).smtp_password_encrypted

    notification_service.update_settings(
        db_session, NotificationSettingsUpdate(ai_score_threshold=70), actor=admin_user
    )
    stored_after = notification_service.get_or_create(db_session)

    assert stored_after.smtp_password_encrypted == stored_before
    assert stored_after.ai_score_threshold == 70


@pytest.fixture()
def blank_mail_settings(db_session):
    """Чистая строка настроек почты внутри откатываемой транзакции теста.

    Просто так она не чистая: `bootstrap_from_env` срабатывает на старте приложения
    (фикстура `client`) и записывает в тестовую БД ящик из `.env` — за пределами отката,
    как и первый администратор. Тесты первичной настройки обязаны стартовать с пустого
    места независимо от того, поднимал ли кто-то приложение раньше в этом прогоне."""

    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = False
    settings.smtp_host = None
    settings.smtp_username = None
    settings.smtp_password_encrypted = None
    settings.from_address = None
    settings.recipients = None
    settings.admin_recipients = None
    db_session.flush()
    return settings


def _env(**overrides):
    """Копия настроек приложения с подменёнными полями почты — трогать реальный `.env`
    в тестах нельзя, а `Settings` кешируется на весь процесс."""

    from app.core.config import get_settings

    return get_settings().model_copy(
        update={
            "notify_enabled": True,
            "notify_smtp_host": "smtp.env.test",
            "notify_smtp_port": 587,
            "notify_smtp_security": "starttls",
            "notify_smtp_username": "noreply@env.test",
            "notify_smtp_password": "s3cret",
            "notify_from_address": "",
            "notify_recipients": "sales@env.test",
            "notify_admin_recipients": "",
            **overrides,
        }
    )


def test_bootstrap_from_env_configures_the_channel(db_session, monkeypatch, blank_mail_settings):
    monkeypatch.setattr(notification_service, "get_settings", lambda: _env())

    settings = notification_service.bootstrap_from_env(db_session)

    assert settings is not None
    assert settings.smtp_host == "smtp.env.test"
    assert settings.smtp_port == 587
    assert settings.smtp_security == "starttls"
    # Адрес отправителя не задан отдельно — берётся логин ящика.
    assert settings.from_address == "noreply@env.test"
    assert settings.recipients == "sales@env.test"
    # Пароль в базе только в зашифрованном виде.
    assert settings.smtp_password_encrypted
    assert "s3cret" not in settings.smtp_password_encrypted
    assert decrypt_secret(settings.smtp_password_encrypted) == "s3cret"


def test_bootstrap_from_env_does_not_overwrite_settings_from_the_interface(
    db_session, monkeypatch, admin_user, blank_mail_settings
):
    """Главное свойство: правки администратора не откатываются при перезапуске сервера."""

    notification_service.update_settings(
        db_session,
        NotificationSettingsUpdate(smtp_host="smtp.manual.test", recipients="manual@test"),
        actor=admin_user,
    )
    monkeypatch.setattr(notification_service, "get_settings", lambda: _env())

    assert notification_service.bootstrap_from_env(db_session) is None

    settings = notification_service.get_or_create(db_session)
    assert settings.smtp_host == "smtp.manual.test"
    assert settings.recipients == "manual@test"


def test_bootstrap_from_env_does_nothing_without_a_host(db_session, monkeypatch, blank_mail_settings):
    monkeypatch.setattr(notification_service, "get_settings", lambda: _env(notify_smtp_host=""))

    assert notification_service.bootstrap_from_env(db_session) is None
    assert notification_service.get_or_create(db_session).smtp_host is None


def test_smtp_check_reports_missing_host_instead_of_raising(db_session, blank_mail_settings):
    ok, message = notification_service.smtp_check(db_session)
    assert ok is False
    assert "SMTP-сервер не задан" in message


def test_smtp_check_returns_the_reason_of_a_connection_failure(
    db_session, monkeypatch, blank_mail_settings
):
    """Диагностика обязана объяснять причину: ради неё команду и запускают."""

    settings = notification_service.get_or_create(db_session)
    settings.smtp_host = "smtp.unreachable.test"
    db_session.flush()

    def _explode(_settings):
        raise TimeoutError("соединение не установлено")

    monkeypatch.setattr(notification_service, "_connect", _explode)

    ok, message = notification_service.smtp_check(db_session)
    assert ok is False
    assert "соединение не установлено" in message


def test_notifications_endpoint_requires_auth(client):
    assert client.get("/notifications").status_code == 401


def test_settings_endpoint_is_admin_only(client, admin_token):
    created = client.post(
        "/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": f"notify_user_{uuid.uuid4().hex[:6]}",
            "password": "NotifyUser123!",
            "full_name": "Обычный пользователь",
            "role": "user",
        },
    )
    assert created.status_code in (200, 201), created.text
    token = client.post(
        "/auth/login",
        json={"username": created.json()["username"], "password": "NotifyUser123!"},
    ).json()["access_token"]

    # Журнал уведомлений виден всем, настройки почты — только администратору.
    assert client.get("/notifications", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert (
        client.get("/notifications/settings", headers={"Authorization": f"Bearer {token}"}).status_code
        == 403
    )
    assert (
        client.get(
            "/notifications/settings", headers={"Authorization": f"Bearer {admin_token}"}
        ).status_code
        == 200
    )


def test_notification_log_entry_is_readable(db_session, configured_mail):
    tender = _tender(db_session)
    notification_service.notify_new_relevant_tender(db_session, tender)

    entry = (
        db_session.query(Notification)
        .filter(Notification.tender_id == tender.id)
        .order_by(Notification.created_at.desc())
        .first()
    )
    assert entry is not None
    assert entry.trigger == NotificationTrigger.NEW_RELEVANT_TENDER.value
    # В теле письма должно быть достаточно, чтобы принять решение, не открывая систему.
    assert tender.external_id in (entry.body or "")
    assert tender.title in (entry.body or "")
