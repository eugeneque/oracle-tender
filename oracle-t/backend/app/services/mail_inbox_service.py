"""Запросы заказчика по нашей заявке из почты площадок (19.11.2026, раздел 115).

Перечень Bitrix24, п. 65: «Поступил ли запрос заказчика о дополнении или уточнении нашей
заявки?». Такой запрос публично не публикуется: площадка присылает его участнику в личный
кабинет и уведомлением на почту. Ящик с уведомлениями читается по IMAP тем же логином и
паролем, что и SMTP («Почта и уведомления»), только чтение (`readonly`), письма не помечаются
прочитанными.

Что считается запросом: тема или текст говорит о запросе заказчика (организатора) на
разъяснение, уточнение, дополнение или предоставление документов **заявки**; разъяснения
документации (это ответы заказчика всем) — не запрос по заявке. Закупка находится по номеру в
письме (реестровый номер ЕИС 11/19 цифр или номер процедуры площадки). Нашлась — запись в
`tender_mail_messages` и событие журнала `DOCUMENT_ADDED` с `data.application_request`.

Ограничения: формулировки уведомлений у площадок разные — разбор по словам, а не по шаблону
каждой площадки; письмо без номера закупки не привязывается (пишется в журнал как
«не привязано»).
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.models.tender import Tender
from app.models.tender_eis_registry import TenderMailMessage

MAX_MESSAGES = 200
FIRST_RUN_DAYS = 14

REQUEST_RE = re.compile(
    r"(?:запрос\w*|требовани\w*)\s+(?:заказчика\s+|организатора\s+)?(?:о\s+|на\s+)?"
    r"(?:разъяснени\w+|уточнени\w+|дополнени\w+|предоставлени\w+|дозапрос\w*)[^.\n]{0,120}?заявк"
    r"|дозапрос\w*|запрос\w*\s+[^.\n]{0,40}к\s+(?:вашей\s+|участнику\s+)?заявк",
    re.I,
)
NOT_REQUEST_RE = re.compile(r"разъяснени\w+\s+(?:положений\s+)?(?:документаци|извещени)", re.I)
NUMBER_RE = re.compile(r"(?<!\d)(\d{19}|\d{11})(?!\d)|\b([A-ZА-Я]{2,5}\d{3}-\d{6,12})\b")


def _decode(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001 - кривая кодировка заголовка не должна ронять разбор
        return value or ""


def message_text(message: email.message.Message) -> str:
    plain, html = [], []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() != "text":
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (html if part.get_content_subtype() == "html" else plain).append(text)
    if plain:
        return "\n".join(plain)
    return "\n".join(BeautifulSoup(h, "html.parser").get_text("\n") for h in html)


def classify(subject: str, body: str) -> bool:
    text = f"{subject}\n{body[:5000]}"
    return bool(REQUEST_RE.search(text)) and not (NOT_REQUEST_RE.search(subject) and not re.search(r"заявк", subject, re.I))


def numbers(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) or m.group(2) for m in NUMBER_RE.finditer(text or "")))


def find_tender(db: Session, found: list[str]) -> Tender | None:
    if not found:
        return None
    return db.scalars(
        select(Tender)
        .where(or_(Tender.registry_number.in_(found), Tender.external_id.in_(found)))
        .order_by(Tender.created_at)
    ).first()


def excerpt(body: str, limit: int = 1500) -> str:
    match = REQUEST_RE.search(body or "")
    start = max(0, (match.start() if match else 0) - 200)
    text = " ".join((body or "")[start:].split())
    return text[:limit]


def handle_message(db: Session, message: email.message.Message) -> TenderMailMessage | None:
    """Разбирает одно письмо: запрос по заявке с номером известной закупки → запись и событие."""

    from app.services import tender_monitor_service

    subject = _decode(message.get("Subject"))
    body = message_text(message)
    if not classify(subject, body):
        return None
    tender = find_tender(db, numbers(f"{subject}\n{body}"))
    if tender is None:
        logger.info(f"Почта площадок: запрос по заявке «{subject[:120]}» не привязан к закупке — номера нет в базе")
        return None
    message_id = (message.get("Message-ID") or f"{subject}|{message.get('Date')}").strip()[:500]
    try:
        received = parsedate_to_datetime(message.get("Date")) if message.get("Date") else None
    except (TypeError, ValueError):
        received = None
    record = TenderMailMessage(
        tender_id=tender.id,
        message_id=message_id,
        kind="APPLICATION_REQUEST",
        sender=_decode(message.get("From"))[:300],
        subject=subject[:2000],
        excerpt=excerpt(body),
        received_at=received,
    )
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        return None  # уже разобрано
    tender_monitor_service.record_events(
        db,
        tender,
        [{
            "event_type": "DOCUMENT_ADDED",
            "field_key": f"mail:{message_id}"[:300],
            "title": "Запрос заказчика по нашей заявке (уведомление площадки)",
            "old_value": None,
            "new_value": record.excerpt,
            "source_document": subject[:500],
            "effective_at": received.isoformat() if received else None,
            "data": {"application_request": True, "sender": record.sender},
            "note": "Письмо площадки в почтовом ящике участника; ответ готовит тендерный отдел в личном кабинете площадки.",
        }],
    )
    return record


def check_inbox(db: Session) -> dict[str, Any]:
    """Читает новые письма ящика. Без настроек IMAP — пустой проход."""

    from app.services.notification_service import get_or_create

    settings = get_or_create(db)
    if not (settings.imap_enabled and settings.imap_host and settings.smtp_username and settings.smtp_password_encrypted):
        return {"checked": 0, "requests": 0}
    found = 0
    checked = 0
    try:
        password = decrypt_secret(settings.smtp_password_encrypted)
        with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port or 993, timeout=60) as box:
            box.login(settings.smtp_username, password)
            box.select(f'"{settings.imap_folder or "INBOX"}"', readonly=True)
            if settings.imap_last_uid:
                status, data = box.uid("search", None, f"UID {int(settings.imap_last_uid) + 1}:*")
            else:
                since = (datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_DAYS)).strftime("%d-%b-%Y")
                status, data = box.uid("search", None, f"SINCE {since}")
            uids = [int(x) for x in (data[0] or b"").split()] if status == "OK" else []
            uids = [u for u in uids if not settings.imap_last_uid or u > settings.imap_last_uid][-MAX_MESSAGES:]
            for uid in uids:
                status, parts = box.uid("fetch", str(uid), "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                checked += 1
                if handle_message(db, email.message_from_bytes(parts[0][1])) is not None:
                    found += 1
                settings.imap_last_uid = uid
        settings.imap_last_error = None
    except Exception as exc:  # noqa: BLE001 - ящик недоступен: повторим при следующем проходе
        settings.imap_last_error = str(exc)[:2000]
        logger.warning(f"Почта площадок не прочитана: {exc}")
    settings.imap_checked_at = datetime.now(timezone.utc)
    db.add(settings)
    db.commit()
    return {"checked": checked, "requests": found}
