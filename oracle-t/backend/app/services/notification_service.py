"""Уведомления по электронной почте (раздел 5.8 ТЗ) и их журнал.

Канал один — SMTP. Второй канал («Compas», открытый вопрос №12 раздела 11 ТЗ) не
реализуется, но и не мешает: наружу торчит одна функция `_dispatch`, и добавление канала —
это ещё одна ветка в ней плюс колонка `channel` в журнале (она уже есть), а не переделка
триггеров.

**Почему уведомление пишется в журнал даже когда почта не настроена.** Пока ящик не задан,
система всё равно фиксирует «здесь сработал бы триггер» со статусом `skipped`. Иначе после
включения почты нельзя ответить на вопрос «а почему нам ничего не приходило раньше» — и
нельзя проверить работу триггеров, не имея рабочего SMTP.

**Дубликаты.** По одному тендеру каждый триггер срабатывает один раз: анализ и расчёт
запускаются повторно (перезапуск задачи, обновление документации), а письмо «новый
релевантный тендер» про тот же тендер второй раз — это шум, из-за которого перестают читать
и остальные письма.
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import SecretStorageError, decrypt_secret, encrypt_secret
from app.models.ai_profile import VERDICT_LABELS, AiProfileScore
from app.models.log import LogLevel
from app.models.notification import (
    TRIGGER_TITLES,
    Notification,
    NotificationSettings,
    NotificationStatus,
    NotificationTrigger,
)
from app.models.tender import Tender, TenderStage
from app.models.user import User
from app.schemas.notification import (
    KnownRecipient,
    NotificationSettingsOut,
    NotificationSettingsUpdate,
)
from app.services.email_html import MAX_HTML_LENGTH, html_to_text, sanitize_email_html
from app.services.audit import log_action

_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

SMTP_TIMEOUT_SECONDS = 20.0
# Одинаковые критические ошибки сыплются пачками (упал источник — упали все его задачи).
# Письмо с тем же заголовком повторяется не чаще, чем раз в этот интервал.
CRITICAL_ERROR_COOLDOWN = timedelta(minutes=30)


def get_or_create(db: Session) -> NotificationSettings:
    settings = db.get(NotificationSettings, _SINGLETON_ID)
    if settings is None:
        settings = NotificationSettings(id=_SINGLETON_ID)
        db.add(settings)
        db.flush()
    return settings


def parse_recipients(raw: str | None) -> list[str]:
    """Список адресов из текстового поля: запятая, точка с запятой или перенос строки —
    заказчик вставляет адреса как придётся, разбирать это должен код, а не человек."""

    if not raw:
        return []
    separators = str.maketrans({";": ",", "\n": ",", "\r": ","})
    return [part.strip() for part in raw.translate(separators).split(",") if part.strip()]


def to_out(db: Session, settings: NotificationSettings) -> NotificationSettingsOut:
    updated_by_user = db.get(User, settings.updated_by_id) if settings.updated_by_id else None
    return NotificationSettingsOut(
        is_enabled=settings.is_enabled,
        is_configured=is_configured(settings),
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_security=settings.smtp_security,
        smtp_username=settings.smtp_username,
        has_password=bool(settings.smtp_password_encrypted),
        from_address=settings.from_address,
        recipients=settings.recipients,
        admin_recipients=settings.admin_recipients,
        trigger_new_relevant=settings.trigger_new_relevant,
        trigger_high_ai_score=settings.trigger_high_ai_score,
        trigger_deadline_soon=settings.trigger_deadline_soon,
        trigger_critical_error=settings.trigger_critical_error,
        trigger_documents_updated=settings.trigger_documents_updated,
        ai_score_threshold=settings.ai_score_threshold,
        deadline_days_threshold=settings.deadline_days_threshold,
        updated_at=settings.updated_at if settings.smtp_host else None,
        updated_by=updated_by_user.full_name if updated_by_user else None,
    )


def is_configured(settings: NotificationSettings) -> bool:
    return bool(settings.smtp_host and settings.from_address and settings.recipients)


def get_settings_out(db: Session) -> NotificationSettingsOut:
    return to_out(db, get_or_create(db))


def update_settings(
    db: Session, payload: NotificationSettingsUpdate, *, actor: User
) -> NotificationSettingsOut:
    """PATCH-семантика, как у настроек Yandex AI Studio: не присланное поле не трогаем,
    присланный пустой пароль означает «оставить прежний» (иначе его пришлось бы вводить
    заново при каждой правке порога)."""

    settings = get_or_create(db)
    fields_set = payload.model_fields_set

    simple_fields = (
        "is_enabled",
        "smtp_host",
        "smtp_port",
        "smtp_security",
        "smtp_username",
        "from_address",
        "recipients",
        "admin_recipients",
        "trigger_new_relevant",
        "trigger_high_ai_score",
        "trigger_deadline_soon",
        "trigger_critical_error",
        "trigger_documents_updated",
        "ai_score_threshold",
        "deadline_days_threshold",
    )
    for field in simple_fields:
        if field in fields_set:
            setattr(settings, field, getattr(payload, field))

    if "smtp_password" in fields_set and payload.smtp_password:
        settings.smtp_password_encrypted = encrypt_secret(payload.smtp_password)

    settings.updated_by_id = actor.id

    log_action(
        db,
        component="notifications",
        action="update_settings",
        result="success",
        # Пароль в перечень изменённых полей попадает как название поля, без значения.
        details=f"Изменены поля: {', '.join(sorted(fields_set)) or '(нет изменений)'}",
        level=LogLevel.INFO,
        user_id=actor.id,
    )
    db.commit()
    db.refresh(settings)
    return to_out(db, settings)


def bootstrap_from_env(db: Session) -> NotificationSettings | None:
    """Первичная настройка почтового ящика из `.env` — по образцу `bootstrap_admin`.

    Ящик-отправитель у установки один: он заводится админом почтового домена вместе с
    сервером, умеет только отправлять и меняется раз в несколько лет. Хранить его *только*
    в БД, как ключ Yandex AI Studio, неудобно: после пересоздания базы (переезд, откат,
    свежий стенд) рассылка молча выключается, и заметить это можно лишь по ненаступившему
    письму.

    **Заполняется ровно один раз.** Если в таблице уже задан SMTP-сервер, `.env` не
    смотрится вовсе — иначе адресаты, добавленные администратором через `/settings`,
    откатывались бы при каждом перезапуске. Чтобы перенастроить ящик из `.env` заново,
    нужно очистить поле «SMTP-сервер» в интерфейсе.

    Пароль сразу переезжает в БД зашифрованным (`.credentials_key`, как и пароли площадок):
    в открытом виде он остаётся только в `.env`, который в git не попадает.
    """

    env = get_settings()
    if not env.notify_smtp_host:
        return None

    settings = get_or_create(db)
    if settings.smtp_host:
        # get_or_create мог создать пустую строку-синглтон — её надо зафиксировать.
        db.commit()
        return None

    settings.is_enabled = env.notify_enabled
    settings.smtp_host = env.notify_smtp_host
    settings.smtp_port = env.notify_smtp_port
    settings.smtp_security = env.notify_smtp_security
    settings.smtp_username = env.notify_smtp_username or None
    settings.from_address = env.notify_from_address or env.notify_smtp_username or None
    settings.recipients = env.notify_recipients or None
    settings.admin_recipients = env.notify_admin_recipients or None

    if env.notify_smtp_password:
        try:
            settings.smtp_password_encrypted = encrypt_secret(env.notify_smtp_password)
        except SecretStorageError as exc:
            # Недоступный ключ шифрования не должен ронять запуск: сервер поднимется,
            # рассылка не заработает, причина будет в журнале и в тесте письма.
            logger.warning(f"Пароль почтового ящика из .env не сохранён: {exc}")

    log_action(
        db,
        component="notifications",
        action="bootstrap_settings",
        result="success",
        details=(
            f"Почтовый канал настроен из .env: {settings.smtp_host}:{settings.smtp_port} "
            f"({settings.smtp_security}), отправитель {settings.from_address}"
            + ("" if settings.recipients else ", получатели не заданы — рассылки не будет")
        ),
        level=LogLevel.INFO,
    )
    db.commit()
    db.refresh(settings)
    logger.info(
        f"Почтовый канал настроен из .env: {settings.smtp_host}:{settings.smtp_port}"
        f" ({settings.smtp_security})"
    )
    return settings


# --- Отправка -------------------------------------------------------------------------


def _connect(settings: NotificationSettings) -> smtplib.SMTP:
    """Соединение с почтовым сервером, доведённое до пройденной авторизации.

    Отдельно от `_send_email`, потому что этими же шагами проверяется ящик (`smtp_check`):
    почти все ошибки настройки — недоступный хост, не тот порт для выбранного шифрования,
    отвергнутый пароль — вылезают здесь, ещё до письма."""

    password = None
    if settings.smtp_password_encrypted:
        try:
            password = decrypt_secret(settings.smtp_password_encrypted)
        except SecretStorageError as exc:
            raise RuntimeError(f"Не удалось расшифровать пароль почтового ящика: {exc}") from exc

    host = settings.smtp_host or ""
    port = settings.smtp_port
    if settings.smtp_security == "ssl":
        context = ssl.create_default_context()
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            host, port, timeout=SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)

    if settings.smtp_security == "starttls":
        server.starttls(context=ssl.create_default_context())
    if settings.smtp_username and password:
        server.login(settings.smtp_username, password)
    return server


def smtp_check(db: Session) -> tuple[bool, str]:
    """Проверка ящика без отправки письма: соединение, шифрование, логин — и всё.

    Нужна там, где проверочное письмо не годится: на сервере без доступа к интерфейсу
    (`python -m app.cli check-mail`) и когда слать письмо просто некому — список
    получателей ещё не заполнен. Ничего в журнал не пишет: это диагностика, а не событие
    системы."""

    settings = get_or_create(db)
    if not settings.smtp_host:
        return False, "SMTP-сервер не задан"
    try:
        server = _connect(settings)
    except Exception as exc:  # noqa: BLE001 - причину показываем как есть, она и нужна
        return False, f"{type(exc).__name__}: {exc}"
    try:
        server.quit()
    except Exception:  # noqa: BLE001
        pass
    return True, (
        f"Соединение с {settings.smtp_host}:{settings.smtp_port} "
        f"({settings.smtp_security}) установлено, логин принят"
    )


def _send_email(
    settings: NotificationSettings,
    recipients: list[str],
    subject: str,
    body: str,
    html: str | None = None,
) -> None:
    """Реальная отправка. Ошибки не глушатся — их обрабатывает `_dispatch`, записывая
    в журнал уведомлений понятную причину.

    С оформленным телом письмо уходит как `multipart/alternative`: почтовый клиент сам
    выбирает часть, которую умеет показать. Текстовая часть обязательна и когда есть HTML —
    без неё письмо охотнее попадает в спам, а часть клиентов покажет пустоту."""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("ORACLE-T", settings.from_address or ""))
    # Адресат может быть записан как «Иванов Иван <ivanov@example.ru>» — заголовок соберёт
    # RFC-форму сам, а smtplib возьмёт из него адресную часть для RCPT TO.
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")

    server = _connect(settings)
    try:
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - разрыв соединения после отправки не отменяет письмо
            pass


def _dispatch(
    db: Session,
    *,
    trigger: NotificationTrigger,
    subject: str,
    body: str,
    tender: Tender | None = None,
    to_admins: bool = False,
    recipients_override: list[str] | None = None,
    html: str | None = None,
    sent_by: User | None = None,
    respect_enabled: bool = True,
) -> Notification:
    """Единая точка отправки: проверяет настройки, шлёт письмо и пишет запись в журнал.

    Никогда не поднимает исключение наружу: уведомление — побочный эффект основной операции
    (раздел 5.9 ТЗ), и падение почтового сервера не должно ронять анализ тендера, из
    которого это уведомление вызвано."""

    settings = get_or_create(db)
    if recipients_override is not None:
        recipients = recipients_override
    else:
        recipients_raw = settings.admin_recipients if to_admins else settings.recipients
        recipients = parse_recipients(recipients_raw)
        # Адреса администраторов не заданы отдельно — критические письма уходят общему списку:
        # молча никуда не отправить хуже, чем отправить чуть более широкому кругу.
        if to_admins and not recipients:
            recipients = parse_recipients(settings.recipients)

    entry = Notification(
        trigger=trigger.value,
        status=NotificationStatus.SKIPPED.value,
        channel="email",
        recipients=", ".join(recipients) or None,
        subject=subject,
        body=body,
        body_html=html,
        tender_id=tender.id if tender else None,
        sent_by_id=sent_by.id if sent_by else None,
    )

    if respect_enabled and not settings.is_enabled:
        entry.error = "Уведомления выключены в настройках"
    # Проверяется не `is_configured`: тому нужны ещё и получатели из настроек, а у письма с
    # явными адресатами их может не быть вовсе — список задан прямо в форме.
    elif not settings.smtp_host or not settings.from_address or not recipients:
        entry.error = "Почтовый канал не настроен (адрес отправителя, сервер или получатели)"
    else:
        try:
            _send_email(settings, recipients, subject, body, html=html)
            entry.status = NotificationStatus.SENT.value
        except Exception as exc:  # noqa: BLE001 - сбой почты не должен ронять вызывающую операцию
            entry.status = NotificationStatus.FAILED.value
            entry.error = str(exc)
            logger.warning(f"Не удалось отправить уведомление «{subject}»: {exc}")

    db.add(entry)
    log_action(
        db,
        component="notifications",
        action=f"notify:{trigger.value}",
        result=entry.status,
        level=LogLevel.INFO if entry.status != NotificationStatus.FAILED.value else LogLevel.WARNING,
        details=f"{subject}{f' — {entry.error}' if entry.error else ''}",
    )
    db.commit()
    return entry


def _already_notified(db: Session, trigger: NotificationTrigger, tender_id: uuid.UUID) -> bool:
    return (
        db.execute(
            select(Notification.id)
            .where(Notification.trigger == trigger.value, Notification.tender_id == tender_id)
            .limit(1)
        ).first()
        is not None
    )


def _tender_lines(tender: Tender) -> list[str]:
    lines = [
        f"Тендер: {tender.title}",
        f"Номер закупки: {tender.external_id}",
    ]
    if tender.customer_name:
        lines.append(f"Заказчик: {tender.customer_name}")
    if tender.price is not None:
        lines.append(f"НМЦК: {tender.price:,.2f} {tender.currency}".replace(",", " "))
    if tender.application_end:
        lines.append(f"Приём заявок до: {tender.application_end:%d.%m.%Y %H:%M}")
    if tender.okpd2_code:
        lines.append(f"ОКПД2: {tender.okpd2_code}")
    if tender.source_url:
        lines.append(f"Источник: {tender.source_url}")
    return lines


# --- Триггеры раздела 5.8 -------------------------------------------------------------


def notify_new_relevant_tender(db: Session, tender: Tender) -> Notification | None:
    """Триггер 1: новый релевантный тендер. Релевантность — по коду ОКПД2 (раздел 5.4 ТЗ),
    поэтому вызывается после анализа, а не при сборе: до разбора документации код обычно
    неизвестен."""

    from app.services.tender_analysis import is_relevant_okpd2

    settings = get_or_create(db)
    if not settings.trigger_new_relevant:
        return None
    if not is_relevant_okpd2(tender.okpd2_code):
        return None
    if _already_notified(db, NotificationTrigger.NEW_RELEVANT_TENDER, tender.id):
        return None

    body = "\n".join(
        [f"{TRIGGER_TITLES[NotificationTrigger.NEW_RELEVANT_TENDER.value]}.", "", *_tender_lines(tender)]
    )
    return _dispatch(
        db,
        trigger=NotificationTrigger.NEW_RELEVANT_TENDER,
        subject=f"Новый релевантный тендер: {tender.title[:120]}",
        body=body,
        tender=tender,
    )


def notify_high_ai_score(db: Session, tender: Tender) -> Notification | None:
    """Триггер 2: высокая AI-оценка по профилю у нового тендера (раздел 5.8 ТЗ).

    С 03.09.2026 порог берётся от `overall_score` (раздел 5.5.1), а не от процента
    победителя: процент отвечает на вопрос «подходит ли прибор», а решение об участии
    принимается по оценке компании целиком. Порог настраивается, по умолчанию 80%.
    """

    settings = get_or_create(db)
    if not settings.trigger_high_ai_score:
        return None

    score = db.execute(
        select(AiProfileScore)
        .where(AiProfileScore.tender_id == tender.id, AiProfileScore.is_current.is_(True))
    ).scalar_one_or_none()
    if score is None or score.overall_score is None:
        return None
    if float(score.overall_score) < settings.ai_score_threshold:
        return None
    if _already_notified(db, NotificationTrigger.HIGH_AI_SCORE, tender.id):
        return None

    dimensions = [
        ("История", score.history_score),
        ("Задача", score.task_score),
        ("Компетенции", score.competencies_score),
    ]
    # «—» вместо нуля у измерения без данных: ноль читается как «проверили и не нашли»,
    # а честный ответ — «считать не из чего» (раздел 5.5.1 ТЗ).
    breakdown = ", ".join(
        f"{name}: {float(value):.0f}%" if value is not None else f"{name}: —"
        for name, value in dimensions
    )

    body = "\n".join(
        [
            f"AI-оценка по профилю: {float(score.overall_score):.1f}% "
            f"(порог {settings.ai_score_threshold}%).",
            f"По измерениям — {breakdown}.",
            f"Вердикт: {VERDICT_LABELS.get(score.verdict or '', '—')}.",
            *([f"Резюме: {score.summary}"] if score.summary else []),
            "",
            *_tender_lines(tender),
        ]
    )
    return _dispatch(
        db,
        trigger=NotificationTrigger.HIGH_AI_SCORE,
        subject=(
            f"Высокая AI-оценка ({float(score.overall_score):.0f}%): {tender.title[:100]}"
        ),
        body=body,
        tender=tender,
    )


def notify_deadlines_soon(db: Session) -> list[Notification]:
    """Триггер 3: до окончания приёма заявок осталось меньше порога (по умолчанию 5 дней).

    Проверяется по расписанию раз в сутки, а не в момент сбора: срок наступает сам по себе,
    без всякого события в системе. Уведомляем только по релевантным тендерам — иначе письмо
    придёт про каждую из тысяч закупок, попавших в базу «на всякий случай»."""

    from app.services.tender_analysis import is_relevant_okpd2

    settings = get_or_create(db)
    if not settings.trigger_deadline_soon:
        return []

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=settings.deadline_days_threshold)
    tenders = (
        db.execute(
            select(Tender).where(
                Tender.application_end.is_not(None),
                Tender.application_end > now,
                Tender.application_end <= horizon,
                # Отклонённые не напоминают о себе: по ним решение уже принято.
                Tender.stage != TenderStage.REJECTED.value,
            )
        )
        .scalars()
        .all()
    )

    sent: list[Notification] = []
    for tender in tenders:
        if not is_relevant_okpd2(tender.okpd2_code):
            continue
        if _already_notified(db, NotificationTrigger.DEADLINE_SOON, tender.id):
            continue
        days_left = (tender.application_end - now).days if tender.application_end else 0
        body = "\n".join(
            [f"До окончания приёма заявок осталось дней: {days_left}.", "", *_tender_lines(tender)]
        )
        sent.append(
            _dispatch(
                db,
                trigger=NotificationTrigger.DEADLINE_SOON,
                subject=f"Приём заявок закрывается через {days_left} дн.: {tender.title[:100]}",
                body=body,
                tender=tender,
            )
        )
    return sent


def notify_critical_error(db: Session, *, subject: str, details: str) -> Notification | None:
    """Триггер 4: критическая ошибка — отдельным адресатам-администраторам (раздел 5.9 ТЗ).

    Свой список получателей: письма о сбоях сборщика нужны тем, кто чинит систему, а не всем,
    кто следит за тендерами."""

    settings = get_or_create(db)
    if not settings.trigger_critical_error:
        return None

    # Тема сравнивается ровно в том виде, в каком она попадает в журнал (с префиксом):
    # иначе защита от повторов ищет несуществующую строку и не срабатывает.
    full_subject = f"ORACLE-T: {subject}"
    recent = db.execute(
        select(Notification.id).where(
            Notification.trigger == NotificationTrigger.CRITICAL_ERROR.value,
            Notification.subject == full_subject,
            Notification.created_at >= datetime.now(timezone.utc) - CRITICAL_ERROR_COOLDOWN,
        ).limit(1)
    ).first()
    if recent is not None:
        return None

    return _dispatch(
        db,
        trigger=NotificationTrigger.CRITICAL_ERROR,
        subject=full_subject,
        body=f"{subject}\n\n{details}",
        to_admins=True,
    )


def notify_documents_updated(db: Session, *, subject: str, body: str) -> Notification | None:
    """Отчёт еженедельной сверки документов по СИ и руководств (правка по итогам показа
    15.09.2026). Уходит общему списку получателей: документами интересуются те же люди, что
    следят за тендерами, — на них строится сопоставление требований. Тему и текст собирает
    `document_registry_service.build_report`; здесь только флаг триггера и отправка."""

    settings = get_or_create(db)
    if not settings.trigger_documents_updated:
        return None
    return _dispatch(db, trigger=NotificationTrigger.DOCUMENTS_UPDATED, subject=subject, body=body)


def send_test_notification(db: Session, *, actor: User) -> Notification:
    """Проверочное письмо из настроек — единственный способ убедиться, что ящик и пароль
    рабочие, не дожидаясь настоящего триггера."""

    entry = _dispatch(
        db,
        trigger=NotificationTrigger.CRITICAL_ERROR,
        subject="ORACLE-T: проверка почтового канала",
        body=(
            "Это проверочное сообщение из раздела «Уведомления» настроек ORACLE-T.\n"
            f"Отправлено пользователем: {actor.full_name}.\n\n"
            "Если письмо дошло — канал настроен верно."
        ),
        to_admins=True,
    )
    return entry


# --- Произвольное письмо администратора ---------------------------------------------


class InvalidRecipients(ValueError):
    """Среди адресатов есть строки, которые не являются адресами."""

    def __init__(self, entries: list[str]) -> None:
        self.entries = entries
        super().__init__(", ".join(entries))


def split_address(entry: str) -> tuple[str, str]:
    """Разбирает «Иванов Иван <ivanov@example.ru>» на имя и адрес.

    Имя поддерживается ради ровно того случая, ради которого его вводят: в поле получателей
    видно, что письмо уходит человеку, а не набору символов. Оно же вместе с адресом
    сохраняется в журнале и оттуда возвращается в подсказки. Запятая внутри имени не
    поддерживается — по ней разделяются сами адресаты."""

    name, address = parseaddr(entry)
    return name.strip(), address.strip()


def validate_recipients(entries: list[str]) -> list[str]:
    """Отсеивает мусор до отправки. Почтовый сервер отвергнет письмо целиком из-за одного
    кривого адреса, и администратор увидит невнятную ошибку SMTP вместо «вот эта строка»."""

    invalid = []
    for entry in entries:
        _, address = split_address(entry)
        if "@" not in address or address.startswith("@") or address.endswith("@") or " " in address:
            invalid.append(entry)
    if invalid:
        raise InvalidRecipients(invalid)
    return entries


def known_recipients(db: Session) -> list[KnownRecipient]:
    """Адреса, которые система уже видела, — для подсказок в поле получателей.

    Источник — журнал уведомлений: если письмо однажды ушло Иванову, его адрес больше не
    нужно вспоминать и вводить руками. Отдельного справочника контактов для этого заводить
    нечего — он немедленно разошёлся бы с тем, куда письма уходят на самом деле.

    Считаются и адреса из неудачных отправок: администратор их уже вводил, и терять их
    из-за упавшего в тот момент почтового сервера бессмысленно. Но `sent_count` растёт
    только на успешных — по нему в подсказках видно, с кем переписка действительно есть.

    Сюда же добавляются адреса из настроек рассылки: пока ручных писем не было, подсказки
    иначе оказались бы пустыми ровно в тот момент, когда нужны.
    """

    # Группировка по строке целиком, а не по адресу: разных комбинаций получателей в журнале
    # единицы, и разбирать их в Python дешевле, чем раскладывать строку средствами SQL.
    rows = db.execute(
        select(
            Notification.recipients,
            Notification.status,
            func.max(Notification.created_at).label("last_at"),
            func.count().label("total"),
        )
        .where(Notification.recipients.is_not(None))
        .group_by(Notification.recipients, Notification.status)
    ).all()

    collected: dict[str, KnownRecipient] = {}

    def _touch(entry: str, *, last_at: datetime | None = None, sent: int = 0) -> None:
        name, address = split_address(entry)
        if "@" not in address:
            return
        key = address.lower()
        item = collected.get(key)
        if item is None:
            collected[key] = KnownRecipient(
                address=address, name=name or None, last_sent_at=last_at, sent_count=sent
            )
            return
        item.sent_count += sent
        # Имя запоминаем первое непустое: администратор мог позже отправить письмо на тот же
        # адрес без имени, и подсказка не должна от этого обезличиваться.
        if name and not item.name:
            item.name = name
        if last_at and (item.last_sent_at is None or last_at > item.last_sent_at):
            item.last_sent_at = last_at

    for recipients_raw, status, last_at, total in rows:
        is_sent = status == NotificationStatus.SENT.value
        for entry in parse_recipients(recipients_raw):
            _touch(entry, last_at=last_at if is_sent else None, sent=total if is_sent else 0)

    settings = get_or_create(db)
    for entry in parse_recipients(settings.recipients) + parse_recipients(settings.admin_recipients):
        _touch(entry)

    # Сверху — те, кому писали недавно: именно их выбирают чаще всего.
    return sorted(
        collected.values(),
        key=lambda item: (item.last_sent_at is not None, item.last_sent_at or datetime.min.replace(tzinfo=timezone.utc), item.sent_count),
        reverse=True,
    )


def send_broadcast(
    db: Session, *, subject: str, body_html: str, recipients: list[str], actor: User
) -> Notification:
    """Письмо, написанное администратором вручную: новости платформы, работы, объявления.

    **Флаг «рассылка включена» здесь не смотрится.** Он выключает автоматические письма по
    триггерам — те уходят сами и в неудачный момент могут только мешать. Ручное письмо
    отправляет человек, который прямо сейчас нажал кнопку: молча положить его в журнал со
    статусом «не отправлено» значило бы соврать в ответ на явное действие.

    Дубликаты не отслеживаются, в отличие от триггеров: повторить объявление — это
    осознанное решение администратора, а не сбой повторного анализа."""

    if len(body_html) > MAX_HTML_LENGTH:
        raise ValueError(
            f"Письмо слишком длинное: {len(body_html)} символов при пределе {MAX_HTML_LENGTH}"
        )

    validate_recipients(recipients)
    safe_html = sanitize_email_html(body_html)
    text = html_to_text(safe_html)
    if not text:
        raise ValueError("Письмо пустое: нет текста для отправки")

    entry = _dispatch(
        db,
        trigger=NotificationTrigger.MANUAL_BROADCAST,
        subject=subject,
        body=text,
        html=safe_html,
        recipients_override=recipients,
        sent_by=actor,
        respect_enabled=False,
    )
    log_action(
        db,
        component="notifications",
        action="broadcast",
        result=entry.status,
        level=LogLevel.INFO if entry.status != NotificationStatus.FAILED.value else LogLevel.WARNING,
        details=f"«{subject}» — получателей: {len(recipients)}",
        user_id=actor.id,
    )
    db.commit()
    return entry
