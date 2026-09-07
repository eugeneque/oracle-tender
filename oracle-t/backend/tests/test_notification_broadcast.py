"""Произвольные письма администратора: редактор, адресаты и подсказки.

Проверяется то, что ломается тихо: HTML из браузерного редактора, уехавший в письмо вместе
со скриптом; письмо, не ушедшее из-за выключенных автоматических уведомлений; и подсказки
адресов, ради которых всё и затевалось.
"""

from __future__ import annotations

import pytest

from app.models.notification import Notification, NotificationStatus, NotificationTrigger
from app.services import notification_service
from app.services.email_html import html_to_text, sanitize_email_html


@pytest.fixture()
def outbox(db_session, monkeypatch):
    """Настроенный канал с подменённой отправкой: тестам нужно, что именно ушло бы."""

    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = True
    settings.smtp_host = "smtp.example.test"
    settings.from_address = "oracle-t@example.test"
    settings.recipients = "sales@example.test"
    settings.admin_recipients = None
    db_session.flush()

    sent: list[dict] = []

    def _capture(_settings, recipients, subject, body, html=None):
        sent.append({"recipients": recipients, "subject": subject, "body": body, "html": html})

    monkeypatch.setattr(notification_service, "_send_email", _capture)
    return sent


# --- Очистка HTML ---------------------------------------------------------------------


def test_script_and_event_handlers_do_not_survive_sanitizing():
    html = sanitize_email_html(
        '<p onclick="steal()">Новость<script>alert(1)</script></p>'
        '<a href="javascript:alert(1)">ссылка</a>'
    )
    assert "script" not in html
    assert "onclick" not in html
    assert "javascript" not in html
    # Текст при этом сохранён целиком: его писал человек.
    assert "Новость" in html and "ссылка" in html


def test_formatting_and_normal_links_survive_sanitizing():
    html = sanitize_email_html(
        '<p><b>Важно</b>: <i>плановые работы</i></p>'
        '<ul><li>суббота</li></ul>'
        '<a href="https://mirtek.ru">подробнее</a>'
    )
    assert "<b>Важно</b>" in html
    assert "<i>плановые работы</i>" in html
    assert "<li>суббота</li>" in html
    assert '<a href="https://mirtek.ru">подробнее</a>' in html


def test_editor_garbage_is_stripped_but_text_stays():
    """Именно это отдаёт `document.execCommand` после вставки из Word."""

    html = sanitize_email_html(
        '<style>.x{color:red}</style><font color="red" size="4">Текст</font>'
        '<div style="margin:0"><span class="w">и ещё</span></div>'
    )
    assert "font" not in html and "style" not in html and "class" not in html
    assert "Текст" in html and "и ещё" in html


def test_unclosed_tags_are_closed():
    assert sanitize_email_html("<p><b>висит") == "<p><b>висит</b></p>"


def test_plain_text_version_keeps_the_structure_of_the_letter():
    text = html_to_text(
        sanitize_email_html("<p>Привет!</p><ul><li>раз</li><li>два</li></ul><p>Хвост</p>")
    )
    assert text == "Привет!\n\n- раз\n- два\n\nХвост"


# --- Отправка -------------------------------------------------------------------------


def test_broadcast_goes_to_the_addresses_from_the_form(db_session, admin_user, outbox):
    entry = notification_service.send_broadcast(
        db_session,
        subject="Плановые работы",
        body_html="<p>В субботу <b>обновление</b>.</p>",
        recipients=["Иванов Иван <ivanov@example.ru>", "petrov@example.ru"],
        actor=admin_user,
    )

    assert entry.status == NotificationStatus.SENT.value
    assert entry.trigger == NotificationTrigger.MANUAL_BROADCAST.value
    assert entry.sent_by_id == admin_user.id
    # Адресаты из формы, а не из настроек рассылки.
    assert outbox[0]["recipients"] == ["Иванов Иван <ivanov@example.ru>", "petrov@example.ru"]
    assert "sales@example.test" not in str(outbox[0]["recipients"])
    # Письмо уходит обеими частями: оформленной и текстовой.
    assert "<b>обновление</b>" in outbox[0]["html"]
    assert outbox[0]["body"] == "В субботу обновление."


def test_broadcast_ignores_the_switch_that_turns_off_automatic_letters(
    db_session, admin_user, outbox
):
    """Флаг «рассылка включена» относится к триггерам. Ручное письмо отправляет человек,
    который прямо сейчас нажал кнопку, — молчать в ответ нельзя."""

    settings = notification_service.get_or_create(db_session)
    settings.is_enabled = False
    db_session.flush()

    entry = notification_service.send_broadcast(
        db_session,
        subject="Объявление",
        body_html="<p>Текст</p>",
        recipients=["ivanov@example.ru"],
        actor=admin_user,
    )
    assert entry.status == NotificationStatus.SENT.value
    assert len(outbox) == 1


def test_broadcast_rejects_addresses_that_are_not_addresses(db_session, admin_user, outbox):
    with pytest.raises(notification_service.InvalidRecipients) as exc:
        notification_service.send_broadcast(
            db_session,
            subject="Объявление",
            body_html="<p>Текст</p>",
            recipients=["ivanov@example.ru", "просто Иванов"],
            actor=admin_user,
        )
    assert exc.value.entries == ["просто Иванов"]
    # Ни одного письма: один кривой адрес не должен превращаться в частичную рассылку,
    # после которой непонятно, кому переотправлять.
    assert outbox == []


def test_broadcast_rejects_a_letter_without_text(db_session, admin_user, outbox):
    """`<p><br></p>` — это пустой редактор в браузере, а не письмо."""

    with pytest.raises(ValueError):
        notification_service.send_broadcast(
            db_session,
            subject="Пусто",
            body_html="<p><br></p>",
            recipients=["ivanov@example.ru"],
            actor=admin_user,
        )
    assert outbox == []


def test_failed_broadcast_is_recorded_instead_of_raising(db_session, admin_user, monkeypatch):
    settings = notification_service.get_or_create(db_session)
    settings.smtp_host = "smtp.example.test"
    settings.from_address = "oracle-t@example.test"
    db_session.flush()

    def _explode(*args, **kwargs):
        raise TimeoutError("почтовый сервер не отвечает")

    monkeypatch.setattr(notification_service, "_send_email", _explode)

    entry = notification_service.send_broadcast(
        db_session,
        subject="Объявление",
        body_html="<p>Текст</p>",
        recipients=["ivanov@example.ru"],
        actor=admin_user,
    )
    assert entry.status == NotificationStatus.FAILED.value
    assert "не отвечает" in (entry.error or "")


# --- Подсказки адресов ----------------------------------------------------------------


def test_known_recipients_remember_everyone_the_system_wrote_to(db_session, admin_user, outbox):
    notification_service.send_broadcast(
        db_session,
        subject="Первое письмо",
        body_html="<p>Текст</p>",
        recipients=["Иванов Иван <ivanov@example.ru>"],
        actor=admin_user,
    )

    known = {item.address: item for item in notification_service.known_recipients(db_session)}

    assert "ivanov@example.ru" in known
    assert known["ivanov@example.ru"].name == "Иванов Иван"
    assert known["ivanov@example.ru"].sent_count == 1
    assert known["ivanov@example.ru"].last_sent_at is not None
    # Адреса из настроек рассылки тоже подсказываются: иначе поле пустует ровно тогда,
    # когда подсказки нужнее всего — до первой ручной рассылки.
    assert "sales@example.test" in known


def test_known_recipients_keep_an_address_even_if_the_letter_failed(db_session, monkeypatch):
    """Адрес администратор уже вводил — терять его из-за упавшего сервера незачем."""

    db_session.add(
        Notification(
            trigger=NotificationTrigger.MANUAL_BROADCAST.value,
            status=NotificationStatus.FAILED.value,
            recipients="sidorov@example.ru",
            subject="Не ушло",
        )
    )
    db_session.flush()

    known = {item.address: item for item in notification_service.known_recipients(db_session)}
    assert known["sidorov@example.ru"].sent_count == 0
    assert known["sidorov@example.ru"].last_sent_at is None


def test_known_recipients_merge_the_same_address_written_differently(db_session):
    for recipients in ("Иванов Иван <ivanov@example.ru>", "IVANOV@example.ru"):
        db_session.add(
            Notification(
                trigger=NotificationTrigger.MANUAL_BROADCAST.value,
                status=NotificationStatus.SENT.value,
                recipients=recipients,
                subject="Письмо",
            )
        )
    db_session.flush()

    matching = [
        item
        for item in notification_service.known_recipients(db_session)
        if item.address.lower() == "ivanov@example.ru"
    ]
    assert len(matching) == 1
    assert matching[0].name == "Иванов Иван"
    assert matching[0].sent_count == 2


# --- Права доступа --------------------------------------------------------------------


def test_broadcast_endpoint_is_admin_only(client, admin_token):
    payload = {"subject": "x", "body_html": "<p>x</p>", "recipients": ["a@b.ru"]}
    assert client.post("/notifications/broadcast", json=payload).status_code == 401
    assert client.get("/notifications/recipients").status_code == 401
    assert (
        client.get(
            "/notifications/recipients", headers={"Authorization": f"Bearer {admin_token}"}
        ).status_code
        == 200
    )


def test_broadcast_endpoint_reports_bad_addresses_as_a_request_error(client, admin_token):
    response = client.post(
        "/notifications/broadcast",
        json={"subject": "Тема", "body_html": "<p>Текст</p>", "recipients": ["не адрес"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422
    assert "не адрес" in response.json()["detail"]
