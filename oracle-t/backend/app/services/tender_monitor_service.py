"""Отслеживание изменений по закупкам (05.11.2026, раздел 84 ARCHITECTURE.md).

Bitrix24 принимает изменения по тендерам в «Журнале событий». Чтобы передать «было → стало»,
по каждой закупке под наблюдением хранится слепок прошлой проверки (`tender_monitors.snapshot`);
проверка заново снимает данные с площадки и из карточки ЕИС, строит новый слепок и сравнивает.

Что сравнивается: срок окончания подачи; даты аукциона, переторжки и подведения итогов (из
карточки ЕИС, с поясом); НМЦК; статус; условия обеспечения (поля карточки); состав документов
(новый файл, новая редакция, протокол, разъяснение, договор, ТЗ).

Первая проверка событий не создаёт: она снимает исходный слепок (в документе Bitrix24 —
«при первичной загрузке изменение не выдумывать»).

С 12.11.2026 (раздел 100) под наблюдение сама встаёт каждая закупка, выгруженная в Bitrix24.
Найденные изменения уходят письмом, важные для оценки помечают разбор неактуальным, а новая
документация и цена ставят повторный разбор с полной выгрузкой (разделы 118, 124).

События отдельных лотов маршрутизируются на сделки лотов (раздел 117). Документ, заменённый
по тому же адресу без изменения карточки ЕИС, по-прежнему не замечается; статус приостановки
берётся из официального этапа и журнала ЕИС, а не из общего статуса коммерческой площадки.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.registry import get_adapter
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.bitrix_deal import BitrixDealLink
from app.models.tender_event import EventDelivery, TenderEvent, TenderEventView, TenderMonitor
from app.models.user import User
from app.services import document_service, eis_card_dates, eis_card_tables, tender_card_service, tender_positions
from app.services.audit import log_action

# Коды событий — по документации Bitrix24 «События СОВА и Bitrix24» (редакция 2026-11-05,
# приложение 1.7.0, schema_version 2): 16 кодов с русскими подписями и два новых для даты итогов
# и общего статуса процедуры. Значение — согласован ли код: событие с несогласованным кодом
# записывается как `held` и не отправляется. Сейчас согласованы все, которые мы формируем.
# Не заменять близкими по смыслу названиями из других документов: у портала именно
# `APPLICATION_END_CHANGED` и `AUCTION_DATE_CHANGED`, а не `DEADLINE_CHANGED`/`AUCTION_CHANGED`.
EVENT_TYPES: dict[str, bool] = {
    "APPLICATION_END_CHANGED": True,
    "AUCTION_DATE_CHANGED": True,
    "REBID_DATE_CHANGED": True,
    "DOCUMENT_ADDED": True,
    "DOCUMENT_REPLACED": True,
    "DOCUMENT_REMOVED": True,
    "TECHNICAL_REQUIREMENT_CHANGED": True,
    "CONTRACT_CHANGED": True,
    "AMOUNT_CHANGED": True,
    "SECURITY_CHANGED": True,
    "CLARIFICATION_PUBLISHED": True,
    "PROCEDURE_SUSPENDED": True,
    "PROCEDURE_RESUMED": True,
    "PROCEDURE_CANCELLED": True,
    "PROTOCOL_PUBLISHED": True,
    "RESULT_CHANGED": True,
    "RESULT_DATE_CHANGED": True,
    "PROCEDURE_STATUS_CHANGED": True,
}

_STATUS_LABELS = {
    "collecting_bids": "приём заявок",
    "evaluation": "рассмотрение заявок",
    "completed": "завершена",
    "cancelled": "отменена",
}

# Изменения, после которых разбор ИИ может оказаться неверным (раздел 100): требования, цена,
# обеспечение, проект договора, разъяснения — то, из чего складываются матрица соответствия
# и оценка. Сроки, даты аукциона, статус и протоколы на оценку соответствия не влияют: о них
# сообщает журнал событий, а разбор остаётся актуальным.
ANALYSIS_AFFECTING_EVENTS = frozenset(
    {
        "AMOUNT_CHANGED",
        "SECURITY_CHANGED",
        "DOCUMENT_ADDED",
        "DOCUMENT_REPLACED",
        "TECHNICAL_REQUIREMENT_CHANGED",
        "CONTRACT_CHANGED",
        "CLARIFICATION_PUBLISHED",
    }
)

# Автоматическое наблюдение снимается, когда закупка отменена или срок подачи прошёл так давно,
# что ждать протоколов уже нечего: каждая проверка — заходы на площадку и в ЕИС.
AUTO_MONITOR_DAYS_AFTER_DEADLINE = 60

# Сколько дней событие считается «новым» для зелёной точки, если журнал так и не открыли:
# иначе у давно забытых закупок точка горела бы вечно.
UNSEEN_EVENTS_DAYS = 30

# Пауза между закупками при плановой проверке: каждая — несколько обращений к площадке и ЕИС.
DELAY_BETWEEN_TENDERS_SECONDS = 2.0


# --- слепок -----------------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="minutes")


def _same_instant(left: str | None, right: str | None) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    try:
        return datetime.fromisoformat(left) == datetime.fromisoformat(right)
    except ValueError:
        return False


def _same_price(left: str | None, right: str | None) -> bool:
    try:
        return Decimal(left or "x") == Decimal(right or "x")
    except InvalidOperation:
        return left == right


def _our_result(db: Session, tender: Tender) -> str | None:
    from app.services.protocol_results_service import our_result

    return our_result(db, tender)


def _position_label(item: Any) -> str:
    number = (item.position_no or "").strip().rstrip(".")
    label = f"{number}. {item.name}" if number else item.name
    return label[:300] + (f" (лот {item.lot_number})" if item.lot_number not in (None, "1") else "")


def _protocol_missing(
    fields: dict[str, Any],
    documents: dict[str, Any],
    card_protocols: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """Дата итогов наступила, а протокола нет (п. 69). Дата без времени ждёт конца суток."""

    value = fields.get("result_date")
    if not value:
        return False
    try:
        expected = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=timezone.utc)
    if (expected.hour, expected.minute, expected.second) == (0, 0, 0):
        expected += timedelta(days=1)
    has_documents = any((item.get("class") == DocumentClass.PROTOCOL.value) for item in documents.values())
    has_card_protocols = any("недейств" not in (item.get("status") or "").lower() for item in card_protocols.values())
    return not has_documents and not has_card_protocols and (now or datetime.now(timezone.utc)) > expected


def build_snapshot(db: Session, tender: Tender) -> dict[str, Any]:
    """Слепок наблюдаемых показателей закупки в виде, пригодном для сравнения и для JSONB."""

    card = db.get(TenderCard, tender.id)
    payload = card.payload if card is not None else None
    dates = eis_card_dates.dates_from_payload(payload)
    # Срок подачи — по карточке ЕИС (точное время и пояс), иначе по выдаче площадки.
    fields = {
        "application_end": _iso(dates.get("application_end") or tender.application_end),
        "auction_date": _iso(dates.get("auction_date")),
        "rebid_date": _iso(dates.get("rebid_date")),
        "result_date": _iso(dates.get("result_date")),
        "price": str(tender.price) if tender.price is not None else None,
        "currency": tender.currency,
        "status": tender.status,
        # Результат нашей заявки по протоколам (раздел 96): None — нас в протоколах нет.
        "our_result": _our_result(db, tender),
    }
    security = {
        name: value.strip()[:500]
        for name, value in eis_card_dates._fields(payload)  # noqa: SLF001 - тот же разбор полей карточки
        if "обеспечени" in name and value.strip()
    }
    documents = {
        doc.source_url: {"name": doc.file_name, "class": doc.document_class}
        for doc in document_service.list_tender_documents(db, tender.id)
    }
    # 17.11.2026 (раздел 113): официальный этап, журнал ЕИС, редакции протоколов и договоров,
    # изменения/разъяснения и позиции спецификации. Сравниваются, только если ключ был и в
    # прошлом слепке: у снятых раньше слепков их нет, и первая проверка не выдумывает событий.
    card_payload = payload or {}
    positions = {
        _position_label(item):
        (str(item.quantity.normalize()) if item.quantity is not None else None) or item.quantity_text or ""
        for item in tender_positions.list_positions(db, tender)
    }
    return {
        "fields": fields,
        "security": security,
        "documents": documents,
        "stage": eis_card_tables.official_stage(card_payload),
        "journal": eis_card_tables.notable_journal(card_payload),
        "card_protocols": eis_card_tables.documents_map(card_payload, "protocols"),
        "card_contracts": eis_card_tables.documents_map(card_payload, "contracts"),
        "card_changes": eis_card_tables.documents_map(card_payload, "changes"),
        "positions": positions,
        "doc_security": _doc_security(db, tender),
        "protocol_missing": _protocol_missing(fields, documents, card_payload and eis_card_tables.documents_map(card_payload, "protocols") or {}),
        **_registry_snapshot(db, tender),
    }


def _registry_snapshot(db: Session, tender: Tender) -> dict[str, Any]:
    """Жалобы и договоры из реестров ЕИС (раздел 115): только если реестры уже читались —
    иначе пустой блок выглядел бы как «жалоб нет», а первое чтение — как новые жалобы."""

    from app.services import eis_registry_service

    data = eis_registry_service.get(db, tender)
    if data["registry_checked_at"] is None:
        return {}
    complaints = {
        item["number"]: {
            "status": item.get("status"), "outcome": item.get("outcome"), "prescription": item.get("prescription"),
            "hearing_at": item.get("hearing_at"), "decision": item.get("decision_number"),
            "decision_form": item.get("decision_form"), "decision_url": item.get("decision_url"),
            "resolutive_url": item.get("resolutive_url"), "full_url": item.get("full_url"),
            "decision_date": item.get("decision_date") or item.get("result_placed"),
            "applicant": item.get("applicant"), "control_body": item.get("control_body"), "placed": item.get("placed"),
        }
        for item in data["complaints"]
    }
    related = {
        item["number"]: {
            "related_to": item["related_to"], "url": item.get("url"),
            "status": item.get("status"), "published": item.get("published"),
        }
        for item in data["customer_history"]
        if item.get("related_to")
    }
    contracts = {}
    for item in data["contracts"]:
        versions = item.get("versions") or []
        last = versions[-1] if versions else item
        contracts[item["number"]] = {
            "version": last.get("version") or 1, "price": last.get("price"), "execution_end": last.get("execution_end"),
            "change_reason": last.get("change_reason"), "change_date": last.get("change_date"), "placed": last.get("placed"),
        }
    return {
        "registry_complaints": complaints,
        "registry_contracts": contracts,
        "registry_related_procedures": related,
    }


def _doc_security(db: Session, tender: Tender) -> dict[str, str]:
    """Обеспечение заявки и договора по разбору документации (раздел 95) — п. 59: условия
    меняются в новой редакции документации, а поля карточки ЕИС у коммерческих площадок пусты."""

    from app.services.contract_conditions_service import get_conditions

    out: dict[str, str] = {}
    for item in get_conditions(db, tender)["conditions"]:
        if item.get("kind") not in ("APPLICATION_SECURITY", "CONTRACT_SECURITY"):
            continue
        parts = [item.get("value"), f"{item['percent']} %" if item.get("percent") is not None else None,
                 f"{item['amount']} руб." if item.get("amount") is not None else None, item.get("allowed_forms")]
        if item.get("presence") == "NOT_REQUIRED":
            parts = ["не требуется"]
        key = f"{item['kind']}:{item.get('lot_number') or '1'}"
        out[key] = "; ".join(p for p in parts if p)[:500]
    return out


# --- сравнение --------------------------------------------------------------------------------


def _change(
    event_type: str,
    field_key: str,
    title: str,
    old: str | None,
    new: str | None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "field_key": field_key,
        "title": title,
        "old_value": old,
        "new_value": new,
        **extra,
    }


_NO_PREVIOUS = "Прежнего значения нет: показатель обнаружен впервые."
_RESULT_TITLES = {
    "WIN": "Опубликована наша победа",
    "LOSS": "Опубликован итог: победил другой участник",
    "NOT_ADMITTED": "Наша заявка не допущена",
    "PENDING": "Рассмотрение нашей заявки продолжается",
}


def _security_kind(name: str) -> str | None:
    """Вид обеспечения по названию поля карточки: заявки или исполнения контракта."""

    lowered = name.lower()
    if "заяв" in lowered:
        return "APPLICATION"
    if any(word in lowered for word in ("исполнен", "контракт", "договор")):
        return "CONTRACT"
    return None


def _document_event(url: str, doc: dict[str, Any], old_docs: dict[str, Any]) -> dict[str, Any]:
    name = doc.get("name") or "документ"
    klass = doc.get("class")
    old_names = {d.get("name") for d in old_docs.values()}
    had_class = any(d.get("class") == klass for d in old_docs.values())
    revised = name in old_names
    lowered = name.lower()
    common = {"source_document": name, "source_url": url}
    # Файл с тем же названием, но другим адресом — вероятно, новая редакция; номер версии не
    # выдумываем (требование Bitrix24).
    revision_note = "Файл с тем же названием, но другим адресом — вероятно, новая редакция." if revised else None

    if klass == DocumentClass.PROTOCOL.value:
        return _change(
            "PROTOCOL_PUBLISHED", f"doc:{url}", f"Опубликован протокол: {name}", None, name,
            data={"protocol_kind": name, "is_final": True if "итогов" in lowered else None},
            note="Итоговый характер определён по названию файла." if "итогов" in lowered
            else "Итоговый ли протокол, по названию файла не определить.",
            **common,
        )
    if "разъяснен" in lowered:
        return _change(
            "CLARIFICATION_PUBLISHED", f"doc:{url}", f"Опубликовано разъяснение: {name}", None, name,
            note="В значении — название файла; содержание разъяснения в самом документе.",
            **common,
        )
    if klass == DocumentClass.CONTRACT.value and had_class:
        return _change(
            "CONTRACT_CHANGED", f"doc:{url}", f"Новая редакция проекта договора: {name}",
            name if revised else None, name,
            note=revision_note or "Опубликован ещё один файл договора; различия пунктов не сверялись.",
            **common,
        )
    # Файл «изменения в ТТ/ТЗ» — новая редакция требований, даже если отдельного ТЗ в слепке не было.
    if klass == DocumentClass.TZ_DESCRIPTION.value and (had_class or document_service._TZ_CHANGE_RE.search(name)):  # noqa: SLF001
        return _change(
            "TECHNICAL_REQUIREMENT_CHANGED", f"doc:{url}", f"Новая редакция ТЗ: {name}",
            name if revised else None, name,
            note=revision_note or "Опубликован ещё один файл ТЗ; изменённые требования не сверялись.",
            **common,
        )
    if revised:
        return _change(
            "DOCUMENT_REPLACED", f"doc:{url}", f"Заменён документ: {name}", name, name,
            note=revision_note, **common,
        )
    return _change(
        "DOCUMENT_ADDED", f"doc:{url}", f"Добавлен документ: {name}", None, name,
        note="Прежнего значения нет: документ опубликован впервые.", **common,
    )


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Изменения между двумя слепками. Чистая функция: без базы и сети."""

    changes: list[dict[str, Any]] = []
    of, nf = old.get("fields") or {}, new.get("fields") or {}

    for field, code, title_first, title_changed in (
        ("application_end", "APPLICATION_END_CHANGED", "Назначен срок окончания подачи заявок",
         "Изменился срок окончания подачи заявок"),
        # Дата пропала из карточки — не событие: чаще это сбой разбора страницы, чем снятие даты.
        ("auction_date", "AUCTION_DATE_CHANGED", "Назначена дата аукциона", "Изменилась дата аукциона"),
        ("rebid_date", "REBID_DATE_CHANGED", "Назначена переторжка", "Изменилась дата переторжки"),
        ("result_date", "RESULT_DATE_CHANGED", "Назначена дата подведения итогов",
         "Изменилась дата подведения итогов"),
    ):
        if nf.get(field) and not _same_instant(of.get(field), nf[field]):
            first = not of.get(field)
            changes.append(
                _change(
                    code, field, title_first if first else title_changed, of.get(field), nf[field],
                    note=_NO_PREVIOUS if first else None,
                )
            )

    if nf.get("price") is not None and not _same_price(of.get("price"), nf["price"]):
        changes.append(
            _change(
                "AMOUNT_CHANGED", "price", "Изменилась НМЦК", of.get("price"), nf["price"],
                data={"currency": nf.get("currency") or "RUB"},
                note=_NO_PREVIOUS if of.get("price") is None else None,
            )
        )

    old_status, new_status = of.get("status"), nf.get("status")
    if new_status and new_status != old_status:
        cancelled = new_status == "cancelled"
        changes.append(
            _change(
                "PROCEDURE_CANCELLED" if cancelled else "PROCEDURE_STATUS_CHANGED",
                "status",
                "Процедура отменена" if cancelled else "Изменился статус процедуры",
                _STATUS_LABELS.get(old_status, old_status),
                _STATUS_LABELS.get(new_status, new_status),
                note="Статус — по классификации СОВА, а не дословная формулировка площадки.",
            )
        )

    # Результат нашей заявки (RESULT_CHANGED, пример `events_06` Bitrix24). Пропавший результат —
    # не событие (как и пропавшая дата): «нет нас в протоколах» не отменяет прежний итог.
    if nf.get("our_result") and nf["our_result"] != of.get("our_result"):
        changes.append(
            _change(
                "RESULT_CHANGED",
                "our_result",
                _RESULT_TITLES.get(nf["our_result"], "Изменился результат нашей заявки"),
                of.get("our_result"),
                nf["our_result"],
                source_document="Протоколы закупки",
                note="Определено разбором протоколов по ИНН наших компаний; итог проверяет тендерный отдел.",
            )
        )

    old_sec, new_sec = old.get("security") or {}, new.get("security") or {}
    for kind in ("APPLICATION", "CONTRACT", None):
        o = {k: v for k, v in old_sec.items() if _security_kind(k) == kind}
        n = {k: v for k, v in new_sec.items() if _security_kind(k) == kind}
        if not n or o == n:
            continue

        def render(data: dict[str, str]) -> str | None:
            return "\n".join(f"{k}: {v}" for k, v in sorted(data.items())) or None

        label = {"APPLICATION": "заявки", "CONTRACT": "исполнения договора", None: ""}[kind]
        changes.append(
            _change(
                "SECURITY_CHANGED",
                f"security:{kind or 'OTHER'}",
                f"Изменились условия обеспечения {label}".strip(),
                render(o),
                render(n),
                data={"security_kind": kind} if kind else {},
                note=None if o else _NO_PREVIOUS,
            )
        )

    old_docs, new_docs = old.get("documents") or {}, new.get("documents") or {}
    # Исходный слепок выгруженной в Bitrix24 закупки снимается из базы (12.11.2026); если её
    # документы ещё не скачивались (`documents_pending`), первая загрузка — не изменение, иначе
    # проверка выдала бы «Добавлен документ» на каждый файл.
    for url, doc in (() if old.get("documents_pending") else new_docs.items()):
        if url not in old_docs:
            changes.append(_document_event(url, doc, old_docs))
    # `DOCUMENT_REMOVED` намеренно не формируем: Bitrix24 просит передавать его только при
    # подтверждённом удалении, а пропажа файла из выдачи площадки чаще означает сбой страницы.
    changes.extend(_card_changes(old, new, changes))
    return changes


# --- этап, журнал и таблицы карточки ЕИС (раздел 113) ---------------------------------------------

_ROUND_CANCEL_RE = re.compile(r"отмен\w*.{0,80}?(переторж|дополнительн\w+\s+ценов|аукцион|торг|ценов\w+\s+предложен)", re.I | re.S)
_PROCEDURE_CANCEL_RE = re.compile(r"отмен\w*\s+(?:определения\s+поставщика|закупки|процедуры)|переведена на этап «[^»]*отменен", re.I)
_FAS_RE = re.compile(r"жалоб|предписан|антимонопол|\bФАС\b", re.I)
_CARD_SOURCE = "Карточка закупки ЕИС"


def _fas_kind(text: str) -> str:
    lowered = text.lower()
    if "предписан" in lowered:
        return "PRESCRIPTION"
    if "решени" in lowered:
        return "DECISION"
    return "COMPLAINT"


_FAS_TITLES = {
    "COMPLAINT": "Подана жалоба на закупку (журнал ЕИС)",
    "DECISION": "Опубликовано решение по жалобе (журнал ЕИС)",
    "PRESCRIPTION": "Выдано предписание по закупке (журнал ЕИС)",
}


def _card_changes(old: dict[str, Any], new: dict[str, Any], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Изменения по этапу, журналу и таблицам карточки ЕИС. Каждый блок сравнивается, только
    если он был и в прошлом слепке (иначе первая проверка после обновления выдала бы всю
    историю закупки событиями)."""

    out: list[dict[str, Any]] = []
    types = lambda: {c["event_type"] for c in found + out}  # noqa: E731 - короткая проверка дублей

    # П. 69: ожидаемая дата итогов прошла, а протокол так и не опубликован. Первый слепок без
    # этого признака события не создаёт; повторные проверки того же отсутствия — тоже.
    if "protocol_missing" in old and new.get("protocol_missing") and not old.get("protocol_missing"):
        expected = (new.get("fields") or {}).get("result_date")
        out.append(_change(
            "PROCEDURE_STATUS_CHANGED", "protocol_missing",
            "Дата подведения итогов прошла, протокол не опубликован", expected, None,
            data={"protocol_missing": True, "expected_result_date": expected},
            source_document=_CARD_SOURCE,
            note="Проверены документы закупки и вкладка протоколов ЕИС; результат по плановой дате не выдумывается."))

    # Официальный этап (пп. 62, 63, 66): формулировка ЕИС главнее классификации СОВЫ.
    if "stage" in old and new.get("stage") and old.get("stage") and new["stage"] != old["stage"]:
        before, after = old["stage"], new["stage"]
        if eis_card_tables.is_suspended(after):
            code, title = "PROCEDURE_SUSPENDED", "Процедура приостановлена"
        elif eis_card_tables.is_suspended(before) and not eis_card_tables.is_cancelled(after):
            code, title = "PROCEDURE_RESUMED", "Процедура возобновлена"
        elif eis_card_tables.is_cancelled(after):
            code, title = "PROCEDURE_CANCELLED", "Процедура отменена"
        else:
            code, title = "PROCEDURE_STATUS_CHANGED", f"Этап закупки: {after}"
        out.append(
            _change(code, "stage", title, before, after, source_document=_CARD_SOURCE,
                    note="Официальная формулировка этапа из карточки ЕИС.")
        )
        # Статус по классификации СОВЫ о том же переходе — дубль официального.
        found[:] = [c for c in found if c.get("field_key") != "status"]

    # Журнал событий ЕИС (пп. 41–47, 62, 66, 67): приостановка, возобновление, жалоба,
    # предписание, отмена раунда — с моментом из самого журнала.
    if "journal" in old:
        for key, entry in (new.get("journal") or {}).items():
            if key in (old.get("journal") or {}):
                continue
            text, at = entry.get("text") or "", entry.get("at")
            common = {"source_document": "Журнал событий закупки ЕИС", "effective_at": at}
            lowered = text.lower()
            if "приостан" in lowered and "PROCEDURE_SUSPENDED" not in types():
                out.append(_change("PROCEDURE_SUSPENDED", "stage", "Процедура приостановлена", None, text[:500],
                                   note="Запись журнала событий ЕИС.", **common))
            elif "возобнов" in lowered and "PROCEDURE_RESUMED" not in types():
                out.append(_change("PROCEDURE_RESUMED", "stage", "Процедура возобновлена", None, text[:500],
                                   note="Запись журнала событий ЕИС.", **common))
            elif _FAS_RE.search(text):
                kind = _fas_kind(text)
                out.append(_change("PROCEDURE_STATUS_CHANGED", f"fas:{key}"[:300], _FAS_TITLES[kind], None, text[:500],
                                   data={"fas_event": kind},
                                   note="Запись журнала событий ЕИС; текст жалобы и решения — в реестре жалоб ЕИС.",
                                   **common))
            elif (match := _ROUND_CANCEL_RE.search(text)) and not _PROCEDURE_CANCEL_RE.search(text):
                rebid = "переторж" in match.group(1).lower() or "дополнительн" in match.group(1).lower()
                field = "rebid_date" if rebid else "auction_date"
                out.append(_change(
                    "REBID_DATE_CHANGED" if rebid else "AUCTION_DATE_CHANGED", field,
                    "Отменена переторжка" if rebid else "Отменён аукцион (раунд торгов)",
                    (old.get("fields") or {}).get(field), None,
                    note="Подтверждённая отмена раунда по журналу ЕИС; процедура в целом не отменена.",
                    data={"round_cancelled": True}, **common))
            elif "отмен" in lowered and "PROCEDURE_CANCELLED" not in types() and _PROCEDURE_CANCEL_RE.search(text):
                out.append(_change("PROCEDURE_CANCELLED", "stage", "Процедура отменена", None, text[:500],
                                   note="Запись журнала событий ЕИС.", **common))

    # Протоколы карточки (п. 84): новая редакция или признание недействующим — файл часто
    # остаётся по тому же адресу, и по составу документов это не видно.
    if "card_protocols" in old:
        before_all = old.get("card_protocols") or {}
        for name, doc in (new.get("card_protocols") or {}).items():
            prev = before_all.get(name)
            if prev is None:
                continue
            if doc.get("revision") and doc.get("revision") != prev.get("revision"):
                out.append(_change(
                    "PROTOCOL_PUBLISHED", f"card-protocol:{name}"[:300], f"Новая редакция протокола: {name}",
                    f"Редакция {prev.get('revision') or '—'}", f"Редакция {doc['revision']}",
                    source_document=name, source_version=doc["revision"], effective_at=doc.get("placed"),
                    data={"protocol_kind": name, "is_final": True if "итог" in name.lower() else None, "is_revision": True},
                    note="Редакция протокола из карточки ЕИС; итоги перечитываются из нового текста."))
            elif (doc.get("status") or "") != (prev.get("status") or "") and "недейств" in (doc.get("status") or "").lower():
                out.append(_change(
                    "PROTOCOL_PUBLISHED", f"card-protocol:{name}"[:300], f"Протокол признан недействующим: {name}",
                    prev.get("status"), doc.get("status"), source_document=name, source_version=doc.get("revision"),
                    data={"protocol_kind": name, "is_revision": True, "is_active": False},
                    note="Статус протокола в карточке ЕИС сменился на «Недействующая»."))

    # Сведения о договоре (пп. 88, 94): договор заключён; новая редакция сведений — изменение
    # условий (допсоглашение) или исполнение.
    if "card_contracts" in old:
        before_all = old.get("card_contracts") or {}
        for name, doc in (new.get("card_contracts") or {}).items():
            prev = before_all.get(name)
            if prev is None:
                out.append(_change(
                    "CONTRACT_CHANGED", f"card-contract:{name}"[:300], f"Опубликованы сведения о заключённом договоре: {name}",
                    None, name, source_document=name, source_version=doc.get("revision"), effective_at=doc.get("placed"),
                    note="Реестр договоров ЕИС: договор по закупке заключён (сведения размещены)."))
            elif doc.get("revision") and doc.get("revision") != prev.get("revision"):
                out.append(_change(
                    "CONTRACT_CHANGED", f"card-contract:{name}"[:300], f"Изменены сведения о договоре: {name}",
                    f"Редакция {prev.get('revision') or '—'}", f"Редакция {doc['revision']}",
                    source_document=name, source_version=doc["revision"], effective_at=doc.get("placed"),
                    note="Новая редакция сведений о договоре в ЕИС: изменение условий (дополнительное соглашение) "
                         "или исполнение; что именно изменилось — в карточке договора ЕИС."))

    # Изменения, разъяснения и запросы заказчика (пп. 52, 60, 65): редакция того же документа и
    # запросы заказчика (по 223-ФЗ — в той же таблице).
    if "card_changes" in old:
        before_all = old.get("card_changes") or {}
        for name, doc in (new.get("card_changes") or {}).items():
            prev = before_all.get(name)
            lowered = name.lower()
            if prev is None and "запрос" in lowered:
                out.append(_change(
                    "DOCUMENT_ADDED", f"card-change:{name}"[:300], f"Запрос заказчика: {name}", None, name,
                    source_document=name, source_version=doc.get("revision"), effective_at=doc.get("placed"),
                    data={"customer_request": True},
                    note="Запрос заказчика из карточки ЕИС. Касается ли он нашей заявки — по тексту запроса."))
            elif prev is not None and doc.get("revision") and doc.get("revision") != prev.get("revision"):
                out.append(_change(
                    "DOCUMENT_REPLACED", f"card-change:{name}"[:300], f"Новая редакция: {name}",
                    f"Редакция {prev.get('revision') or '—'}", f"Редакция {doc['revision']}",
                    source_document=name, source_version=doc["revision"], effective_at=doc.get("placed"),
                    note="Номер редакции — из карточки ЕИС."))

    # Обеспечение по документации (п. 59) — после нового разбора документации.
    old_ds, new_ds = old.get("doc_security"), new.get("doc_security")
    if old_ds and new_ds:
        for key in sorted(set(old_ds) | set(new_ds)):
            if key in new_ds and old_ds.get(key) != new_ds[key]:
                kind = "APPLICATION" if key.startswith("APPLICATION") else "CONTRACT"
                label = "заявки" if kind == "APPLICATION" else "исполнения договора"
                out.append(_change(
                    "SECURITY_CHANGED", f"doc-security:{key}", f"Изменились условия обеспечения {label} (документация)",
                    old_ds.get(key), new_ds[key], data={"security_kind": kind},
                    note="По разбору документации (новая редакция); поля карточки ЕИС могли не измениться."))

    # Реестр жалоб ЕИС (пп. 41–47): новая жалоба, назначено рассмотрение, итог, предписание.
    if "registry_complaints" in old:
        before_all = old.get("registry_complaints") or {}
        for number, item in (new.get("registry_complaints") or {}).items():
            prev = before_all.get(number)
            common = {"source_document": f"Реестр жалоб ЕИС, жалоба № {number}"}
            if prev is None:
                out.append(_change(
                    "PROCEDURE_STATUS_CHANGED", f"fas:{number}:filed", f"Подана жалоба № {number}", None,
                    f"{item.get('applicant') or 'Заявитель не указан'} → {item.get('control_body') or 'орган контроля'}",
                    data={"fas_event": "COMPLAINT", "complaint_number": number}, effective_at=item.get("placed"),
                    note=f"Статус в реестре: {item.get('status') or 'не указан'}.", **common))
            if item.get("hearing_at") and item.get("hearing_at") != (prev or {}).get("hearing_at"):
                out.append(_change(
                    "PROCEDURE_STATUS_CHANGED", f"fas:{number}:hearing", f"Назначено рассмотрение жалобы № {number}",
                    (prev or {}).get("hearing_at"), item["hearing_at"],
                    data={"fas_event": "HEARING", "complaint_number": number}, **common))
            if item.get("outcome") and item.get("outcome") != (prev or {}).get("outcome"):
                out.append(_change(
                    "PROCEDURE_STATUS_CHANGED", f"fas:{number}:decision", f"Решение по жалобе № {number}: {item['outcome']}",
                    (prev or {}).get("outcome"), item["outcome"],
                    data={"fas_event": "DECISION", "complaint_number": number, "decision_number": item.get("decision")}, **common))
            if item.get("decision_form") and (
                item.get("decision_form") != (prev or {}).get("decision_form")
                or item.get("decision_url") != (prev or {}).get("decision_url")
            ):
                full = item["decision_form"] == "FULL"
                out.append(_change(
                    "PROCEDURE_STATUS_CHANGED", f"fas:{number}:decision-form",
                    f"Опубликован {'полный текст решения' if full else 'резолютивная часть решения'} по жалобе № {number}",
                    (prev or {}).get("decision_form"), item["decision_form"],
                    data={
                        "fas_event": "DECISION", "complaint_number": number,
                        "decision_form": item["decision_form"], "decision_url": item.get("decision_url"),
                    },
                    effective_at=item.get("decision_date"), source_url=item.get("decision_url"), **common))
            if item.get("prescription") is True and (prev or {}).get("prescription") is not True:
                out.append(_change(
                    "PROCEDURE_STATUS_CHANGED", f"fas:{number}:prescription", f"Выдано предписание по жалобе № {number}",
                    None, "Предписание выдано", data={"fas_event": "PRESCRIPTION", "complaint_number": number}, **common))

    # Повторная публикация после жалобы (п. 47): связь подтверждена текстом новой карточки ЕИС,
    # а не совпадением заказчика/ОКПД2. Портал создаёт связанную сделку по новому номеру.
    if "registry_related_procedures" in old:
        for number, item in (new.get("registry_related_procedures") or {}).items():
            if number in (old.get("registry_related_procedures") or {}):
                continue
            complaints = new.get("registry_complaints") or {}
            complaint_number = next(iter(complaints), None)
            out.append(_change(
                "PROCEDURE_STATUS_CHANGED", f"fas:repeat:{number}"[:300],
                f"Опубликована повторная закупка № {number}", item["related_to"], number,
                data={
                    "fas_event": "REPEAT_PUBLICATION", "complaint_number": complaint_number,
                    "related_procedure_number": number, "related_procedure_url": item.get("url"),
                    "relation": "REPEAT_AFTER_COMPLAINT", "procedure_status": item.get("status"),
                },
                effective_at=item.get("published"), source_url=item.get("url"),
                source_document="Карточка повторной закупки ЕИС",
                note="Связь с прежней процедурой прямо указана в карточке новой закупки."))

    # Реестр договоров ЕИС (пп. 90, 94): новая редакция сведений — что изменилось.
    if "registry_contracts" in old:
        before_all = old.get("registry_contracts") or {}
        for number, item in (new.get("registry_contracts") or {}).items():
            prev = before_all.get(number)
            if prev is None or item.get("version") == prev.get("version"):
                continue
            diff = [
                {"key": field, "field": label, "old_value": prev.get(field), "new_value": item.get(field)}
                for field, label in (("price", "Цена договора"), ("execution_end", "Окончание исполнения"))
                if prev.get(field) != item.get(field)
            ]
            out.append(_change(
                "CONTRACT_CHANGED", f"registry-contract:{number}",
                f"Договор № {number}: {item.get('change_reason') or 'новая редакция сведений'}",
                f"Редакция {prev.get('version')}", f"Редакция {item.get('version')}",
                source_document=f"Реестр договоров ЕИС, № {number}", source_version=str(item.get("version")),
                effective_at=item.get("placed"), data={"changes": diff, "change_reason": item.get("change_reason")},
                note=("Изменились: " + ", ".join(d["field"] for d in diff) + ".") if diff
                else "Цена и срок исполнения не изменились."))

    # Позиции спецификации (пп. 53, 55): состав и количество после нового разбора документации.
    old_pos, new_pos = old.get("positions"), new.get("positions")
    if old_pos and new_pos and old_pos != new_pos:
        diff = []
        for label in list(old_pos) + [k for k in new_pos if k not in old_pos]:
            if old_pos.get(label) != new_pos.get(label):
                diff.append({"key": _key_of(label), "field": label[:300],
                             "old_value": old_pos.get(label) if label in old_pos else None,
                             "new_value": new_pos.get(label) if label in new_pos else None})
        added = sum(1 for d in diff if d["old_value"] is None and d["field"] not in old_pos)
        removed = sum(1 for d in diff if d["field"] not in new_pos)
        changed = len(diff) - added - removed
        render = lambda data: "\n".join(f"{k}: {v or '—'}" for k, v in list(data.items())[:40])  # noqa: E731
        lots_touched = {
            (m.group(1) if (m := re.search(r"\(лот (\S+)\)$", d["field"])) else "1") for d in diff
        }
        multi_lot = any(re.search(r"\(лот \S+\)$", label) for label in list(old_pos) + list(new_pos))
        lot_scope = {"scope": "LOT", "lot_number": next(iter(lots_touched))} if multi_lot and len(lots_touched) == 1 else {}
        out.append(_change(
            "TECHNICAL_REQUIREMENT_CHANGED", "positions", "Изменился состав или количество позиций",
            render(old_pos)[:3000], render(new_pos)[:3000],
            data={"changes": diff[:50]}, **lot_scope,
            note=f"Позиций добавлено {added}, убрано {removed}, изменено количество у {changed} — по разбору спецификации."))
    return out


def _key_of(label: str) -> str:
    return "pos-" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:10]


# --- повторный сбор данных --------------------------------------------------------------------


def _refresh_fields(db: Session, tender: Tender) -> None:
    """Поля закупки со страницы площадки (цена, статус, сроки) — как при плановом опросе, но
    только для этой закупки, а не поиском по выдаче."""

    from app.services.tender_service import _UPDATABLE_FIELDS

    adapter = get_adapter(tender.source.adapter_key)
    if adapter is None:
        return
    details = adapter.get_tender_details(tender.external_id)
    for name in _UPDATABLE_FIELDS:
        value = getattr(details, name, None)
        if value is None or getattr(tender, name) == value:
            continue
        if eis_card_dates.keep_card_deadline(db, tender, name, value):
            continue
        setattr(tender, name, value)
    db.add(tender)


def _refresh_documents(db: Session, tender: Tender) -> None:
    """Дописывает документы, которых ещё нет у закупки. Уже сохранённые не трогаются."""

    existing = document_service.list_tender_documents(db, tender.id)
    if not existing:
        document_service.sync_tender_documents_with_status(db, tender)
        return
    adapter = get_adapter(tender.source.adapter_key)
    if adapter is None:
        return
    refs = adapter.download_documents(tender.external_id, tender.source_url)
    known = {doc.source_url for doc in existing}
    tender_dir = document_service.get_storage_root() / str(tender.id)
    tender_dir.mkdir(parents=True, exist_ok=True)
    for ref in refs:
        if ref.url in known:
            continue
        known.add(ref.url)
        doc = TenderDocument(
            tender_id=tender.id,
            file_name=ref.file_name,
            file_type=document_service._guess_extension(ref.file_name, ref.url),  # noqa: SLF001
            source_url=ref.url,
            document_class=document_service.classify_document(ref.file_name),
        )
        try:
            with db.begin_nested():
                db.add(doc)
                db.flush()
        except IntegrityError:
            continue
        document_service._process_document(doc, tender_dir)  # noqa: SLF001
    db.commit()


def _refresh_protocols(db: Session, tender: Tender) -> None:
    from app.services.protocol_results_service import refresh_if_new

    refresh_if_new(db, tender)


def _refresh_registries(db: Session, tender: Tender) -> None:
    from app.services import eis_registry_service

    eis_registry_service.refresh(db, tender)


def refresh_tender(db: Session, tender: Tender) -> list[str]:
    """Заново собирает данные закупки. Сбой одного источника не отменяет остальные —
    возвращает тексты ошибок."""

    errors: list[str] = []
    for label, step in (
        ("страница площадки", lambda: _refresh_fields(db, tender)),
        ("карточка ЕИС", lambda: tender_card_service.sync_card(db, tender, force=True)),
        ("документы", lambda: _refresh_documents(db, tender)),
        # Новый протокол — сразу разбор: иначе слепок не увидит смену нашего результата до
        # следующего ручного анализа. Модель вызывается только при новом файле протокола.
        ("протоколы", lambda: _refresh_protocols(db, tender)),
        # Реестры жалоб и договоров ЕИС и история заказчика (раздел 115) — со своим интервалом.
        ("реестры ЕИС", lambda: _refresh_registries(db, tender)),
    ):
        try:
            step()
            db.commit()
        except Exception as exc:  # noqa: BLE001 - изоляция сбоя одного источника
            db.rollback()
            errors.append(f"{label}: {exc}")
            logger.warning(f"Отслеживание {tender.external_id}: {label} не обновлено — {exc}")
    return errors


# --- проверка ---------------------------------------------------------------------------------


def get_monitor(db: Session, tender_id: uuid.UUID) -> TenderMonitor | None:
    return db.get(TenderMonitor, tender_id)


def enable(db: Session, tender: Tender, *, actor: User | None) -> TenderMonitor:
    """Ставит закупку под наблюдение и снимает исходный слепок (если его ещё нет)."""

    monitor = get_monitor(db, tender.id)
    if monitor is None:
        monitor = TenderMonitor(tender_id=tender.id, started_by_id=actor.id if actor else None)
        db.add(monitor)
    monitor.enabled = True
    # Включил человек — и снимать будет человек, а не правило «закупка завершена».
    monitor.auto = False
    db.commit()
    check_tender(db, tender)
    log_action(
        db,
        component="monitoring",
        action=f"enable_monitoring:{tender.external_id}",
        result="success",
        user_id=actor.id if actor else None,
    )
    db.commit()
    db.refresh(monitor)
    return monitor


def disable(db: Session, tender: Tender, *, actor: User | None) -> TenderMonitor | None:
    monitor = get_monitor(db, tender.id)
    if monitor is not None:
        monitor.enabled = False
        log_action(
            db,
            component="monitoring",
            action=f"disable_monitoring:{tender.external_id}",
            result="success",
            user_id=actor.id if actor else None,
        )
        db.commit()
    return monitor


def _last_event_id(db: Session, tender_id: uuid.UUID, field_key: str) -> str | None:
    return db.scalar(
        select(TenderEvent.event_id)
        .where(TenderEvent.tender_id == tender_id, TenderEvent.field_key == field_key)
        .order_by(TenderEvent.detected_at.desc(), TenderEvent.created_at.desc())
        .limit(1)
    )


def _parse_at(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def record_events(
    db: Session, tender: Tender, changes: list[dict[str, Any]], *, detected_at: datetime | None = None
) -> list[TenderEvent]:
    detected_at = detected_at or datetime.now(timezone.utc)
    events: list[TenderEvent] = []
    for change in changes:
        event_type = change["event_type"]
        agreed = EVENT_TYPES.get(event_type, False)
        event = TenderEvent(
            tender_id=tender.id,
            event_id=f"sova-evt-{uuid.uuid4().hex}",
            event_type=event_type,
            field_key=change["field_key"],
            title=change["title"][:300],
            # Лот — только если изменение точно относится к одному лоту (п. 50); иначе процедура.
            scope=change.get("scope") or "PROCEDURE",
            lot_number=change.get("lot_number") if change.get("scope") == "LOT" else None,
            old_value=change.get("old_value"),
            new_value=change.get("new_value"),
            detected_at=detected_at,
            data=change.get("data") or None,
            source_document=(change.get("source_document") or None),
            source_url=change.get("source_url") or tender.source_url,
            note=change.get("note"),
            effective_at=_parse_at(change.get("effective_at")),
            source_version=(str(change["source_version"])[:100] if change.get("source_version") else None),
            supersedes_event_id=_last_event_id(db, tender.id, change["field_key"]),
            delivery=EventDelivery.PENDING.value if agreed else EventDelivery.HELD.value,
        )
        db.add(event)
        db.flush()
        events.append(event)
    return events


_TEXT_DIFF_EVENTS = {"TECHNICAL_REQUIREMENT_CHANGED", "CONTRACT_CHANGED", "DOCUMENT_REPLACED"}


def enrich_document_changes(db: Session, tender: Tender, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Содержание изменений документов (раздел 113, пп. 52–54, 60, 35): новая редакция ТЗ или
    договора сравнивается по тексту с прежней (`document_diff`), разъяснение — кратким
    содержанием и тем, на что оно влияет. Без текста событие остаётся как было."""

    from app.services import document_diff

    docs = None
    for change in changes:
        key = change.get("field_key") or ""
        if not key.startswith("doc:"):
            continue
        if change["event_type"] not in _TEXT_DIFF_EVENTS | {"CLARIFICATION_PUBLISHED"}:
            continue
        if docs is None:
            docs = list(document_service.list_tender_documents(db, tender.id))
        url = key[4:]
        current = next((d for d in docs if d.source_url == url), None)
        if current is None or not current.extracted_text:
            continue
        if change["event_type"] == "CLARIFICATION_PUBLISHED":
            excerpt = document_diff.clarification_excerpt(current.extracted_text)
            areas = document_diff.impact_areas(excerpt)
            if excerpt:
                change["new_value"] = excerpt
                change["data"] = {**(change.get("data") or {}), "affects": areas, "document": current.file_name}
                change["note"] = (
                    "Содержание — из текста разъяснения (без пересказа). Влияет на: "
                    + (", ".join(areas) if areas else "по словам текста не определить") + "."
                )
            continue
        previous = [
            d for d in docs
            if d.id != current.id and d.extracted_text
            and (d.document_class == current.document_class or d.file_name == current.file_name)
            and (d.created_at or now_utc()) <= (current.created_at or now_utc())
        ]
        if not previous:
            continue
        prior = max(previous, key=lambda d: d.created_at or now_utc())
        diff = document_diff.diff_texts(prior.extracted_text, current.extracted_text)
        if not diff["total"]:
            change["note"] = f"Текст совпадает с прежней редакцией «{prior.file_name}»: содержательных изменений нет."
            continue
        before, after = document_diff.summary_values(diff)
        change["old_value"], change["new_value"] = before, after
        change["data"] = {**(change.get("data") or {}), "changes": diff["changes"], "compared_with": prior.file_name}
        change["note"] = (
            f"Сравнение с «{prior.file_name}»: изменено фрагментов — {diff['total']}"
            + (f", показаны первые {len(diff['changes'])}" if diff["total"] > len(diff["changes"]) else "")
            + (". Изменений очень много — возможно, другой документ или другое распознавание; нужна проверка." if diff["noisy"] else ".")
        )
    # После сравнения текстов: продление гарантии видно по изменённым фрагментам о сроках.
    _bank_guarantee_notes(db, tender, changes)
    return changes


def _bank_guarantee_notes(db: Session, tender: Tender, changes: list[dict[str, Any]]) -> None:
    """п. 32: срок сдвинулся — нужно ли продлевать банковскую гарантию. Гарантия должна
    покрывать новый срок, если документация допускает её как форму обеспечения; решение о
    продлении — за сотрудником, здесь только пометка с основанием."""

    from app.services.contract_conditions_service import get_conditions

    conditions = get_conditions(db, tender)["conditions"]

    def guarantee(kind: str) -> dict[str, Any] | None:
        return next(
            (c for c in conditions if c.get("kind") == kind and "гарант" in (c.get("allowed_forms") or c.get("value") or "").lower()),
            None,
        )

    for change in changes:
        shift = None
        if change["event_type"] == "APPLICATION_END_CHANGED" and change.get("old_value") and change.get("new_value"):
            try:
                shift = datetime.fromisoformat(change["new_value"]) - datetime.fromisoformat(change["old_value"])
            except ValueError:
                shift = None
            item = guarantee("APPLICATION_SECURITY")
            if shift and shift.total_seconds() > 0 and item:
                days = shift.days or 1
                change["data"] = {**(change.get("data") or {}), "bank_guarantee_check": "APPLICATION"}
                change["note"] = ((change.get("note") or "") + (
                    f" Срок подачи сдвинут на {days} дн. позже; обеспечение заявки допускается банковской гарантией — "
                    "проверьте, что срок действия выданной гарантии покрывает новые даты (продление — решение тендерного отдела)."
                )).strip()
        elif change["event_type"] == "CONTRACT_CHANGED" and re.search(r"срок", f"{change.get('old_value')}{change.get('new_value')}", re.I):
            item = guarantee("CONTRACT_SECURITY")
            if item:
                change["data"] = {**(change.get("data") or {}), "bank_guarantee_check": "CONTRACT"}
                change["note"] = ((change.get("note") or "") + (
                    " Изменение касается сроков, обеспечение договора допускается банковской гарантией — "
                    "проверьте, требуется ли продление гарантии."
                )).strip()
    _guarantee_coverage_notes(db, tender, changes)


def _guarantee_coverage_notes(db: Session, tender: Tender, changes: list[dict[str, Any]]) -> None:
    """Выданные гарантии (раздел 115): срок подачи или исполнения сдвинулся — покрывает ли
    срок действия внесённой гарантии новую дату. Ответ точный, а не «проверьте»."""

    from app.services import guarantee_service

    guarantees = guarantee_service.list_for(db, tender)
    if not guarantees:
        return
    for change in changes:
        if change["event_type"] == "APPLICATION_END_CHANGED" and change.get("new_value"):
            kind, required, margin = "APPLICATION", change["new_value"], timedelta(0)
        elif change["event_type"] == "CONTRACT_CHANGED" and any(
            c.get("key") == "execution_end" for c in (change.get("data") or {}).get("changes") or []
        ):
            new_end = next(c["new_value"] for c in change["data"]["changes"] if c.get("key") == "execution_end")
            kind, required, margin = "CONTRACT", new_end, guarantee_service.CONTRACT_MARGIN
        else:
            continue
        for item in (g for g in guarantees if g.kind == kind):
            result = guarantee_service.coverage(item, required, margin=margin)
            if result["covered"] is None:
                continue
            change["data"] = {
                **(change.get("data") or {}),
                "bank_guarantee_check": kind,
                "guarantee_extension_required": not result["covered"],
            }
            label = f"Гарантия{(' № ' + item.number) if item.number else ''}"
            change["note"] = ((change.get("note") or "") + " " + (
                f"{label}: {result['basis']} — требуется продление." if not result["covered"]
                else f"{label}: {result['basis']} — новый срок покрывает."
            )).strip()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def check_tender(db: Session, tender: Tender, *, refresh: bool = True) -> list[TenderEvent]:
    """Одна проверка закупки: сбор → слепок → сравнение → события. Первая проверка только
    снимает слепок."""

    monitor = get_monitor(db, tender.id)
    if monitor is None:
        raise LookupError("Закупка не поставлена под наблюдение")
    errors = refresh_tender(db, tender) if refresh else []
    new_snapshot = build_snapshot(db, tender)
    events: list[TenderEvent] = []
    now = datetime.now(timezone.utc)
    if monitor.snapshot:
        changes = enrich_document_changes(db, tender, diff_snapshots(monitor.snapshot, new_snapshot))
        events = record_events(db, tender, changes, detected_at=now)
    else:
        monitor.snapshot_at = now
    monitor.snapshot = new_snapshot
    monitor.last_checked_at = now
    monitor.checks_count = (monitor.checks_count or 0) + 1
    monitor.last_error = "; ".join(errors)[:2000] if errors else None
    db.add(monitor)
    if events:
        log_action(
            db,
            component="monitoring",
            action=f"check_tender:{tender.external_id}",
            result="success",
            level=LogLevel.INFO,
            details="; ".join(f"{e.event_type}: {e.title}" for e in events)[:1500],
        )
    db.commit()
    if events:
        _notify(db, tender, events)
        _schedule_reanalysis(db, tender, events)
    return events


# События о документах, после которых разбор и выгрузка в Bitrix24 пересчитываются сами
# (раздел 118): новые требования, проект договора, разъяснения и просто новые файлы документации.
REANALYSIS_EVENTS = frozenset(
    {
        "AMOUNT_CHANGED",
        "DOCUMENT_ADDED",
        "DOCUMENT_REPLACED",
        "TECHNICAL_REQUIREMENT_CHANGED",
        "CONTRACT_CHANGED",
        "CLARIFICATION_PUBLISHED",
    }
)


def _schedule_reanalysis(db: Session, tender: Tender, events: list[TenderEvent]) -> None:
    """Новая документация → полный разбор заново, после него — полная выгрузка в Bitrix24.

    Раньше пересчёта не было: Bitrix24 получал событие «новая редакция ТЗ», а требования, документы
    и матрица в сделке оставались прежними (замечание коллеги по сделке 2062). Ставится только
    если у закупки уже есть разбор (иначе обновлять нечего — `analysis_outdated_events` пуст) и
    только для закупок, выгруженных в Bitrix24. Расходует токены ИИ — отключается настройкой
    `monitor_auto_reanalyze`. Сбой постановки проверку не отменяет."""

    from app.core.config import get_settings
    from app.core.jobs import enqueue
    from app.models.job import JobKind
    from app.services import tender_twins

    if not get_settings().monitor_auto_reanalyze:
        return
    if not any(e.event_type in REANALYSIS_EVENTS for e in events):
        return
    try:
        link = db.get(BitrixDealLink, tender.id)
        if link is None or not link.deal_id:
            return
        if not analysis_outdated_events(db, tender, among=[e for e in events if e.event_type in REANALYSIS_EVENTS]):
            return
        subject = tender_twins.subject(db, tender)
        enqueue(
            db,
            kind=JobKind.TENDER_FULL_REVIEW,
            tender=subject,
            actor=None,
            payload={"push_bitrix_tender_id": str(tender.id), "reason": "monitor_documents"},
        )
        log_action(
            db,
            component="monitoring",
            action=f"reanalysis_queued:{tender.external_id}",
            result="success",
            level=LogLevel.INFO,
            details="Новая документация: повторный полный разбор и выгрузка в Bitrix24.",
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - пересчёт вторичен, события уже записаны
        db.rollback()
        logger.warning(f"Повторный разбор {tender.external_id} не поставлен: {exc}")


def push_after_review(db: Session, tender_id: uuid.UUID) -> str | None:
    """Полная выгрузка в Bitrix24 после повторного разбора (вызывает обработчик задачи).
    От имени того, кто выгрузил сделку или поставил закупку под наблюдение; без такого
    пользователя или при выключенной отправке пакетов — ничего не делает."""

    from app.services import bitrix_ai_service

    tender = db.get(Tender, tender_id)
    if tender is None:
        return None
    link = db.get(BitrixDealLink, tender.id)
    monitor = get_monitor(db, tender.id)
    actor_id = (link.pushed_by_id if link else None) or (monitor.started_by_id if monitor else None)
    actor = db.get(User, actor_id) if actor_id else None
    if actor is None:
        return "выгрузка в Bitrix24 пропущена: неизвестен пользователь"
    try:
        bitrix_ai_service.send(db, tender, actor=actor, full_snapshot=True)
    except bitrix_ai_service.BitrixAiPushDisabledError:
        return "выгрузка в Bitrix24 выключена"
    return "полная выгрузка в Bitrix24 отправлена"


def _notify(db: Session, tender: Tender, events: list[TenderEvent]) -> None:
    from app.services import notification_service

    try:
        outdated = bool(analysis_outdated_events(db, tender, among=events))
        notification_service.notify_tender_changed(db, tender, events, analysis_outdated=outdated)
    except Exception as exc:  # noqa: BLE001 - письмо — побочный эффект, проверку не отменяет
        db.rollback()
        logger.warning(f"Письмо об изменениях {tender.external_id} не отправлено: {exc}")


# --- разбор ИИ и изменения --------------------------------------------------------------------


def _group_ids(db: Session, tender: Tender) -> list[uuid.UUID]:
    """Записи одной закупки (раздел 93): разбор хранится у основной, а события — у той, что
    выгружена в Bitrix24. Сверять нужно по всей группе."""

    from app.services import tender_twins

    subject = tender_twins.subject(db, tender)
    return [subject.id, *(twin.id for twin in tender_twins.twins(db, subject))]


def analysis_outdated_events(
    db: Session, tender: Tender, *, among: list[TenderEvent] | None = None
) -> list[TenderEvent]:
    """Важные для оценки изменения, обнаруженные после текущего разбора ИИ.

    Пусто — разбор актуален (или его нет: устареть нечему). Флаг не хранится, а вычисляется:
    новый разбор сам «снимает» пометку, и её не нужно сбрасывать в каждом месте, где
    пересчитывается оценка."""

    from app.services import ai_profile_service, tender_twins

    subject = tender_twins.subject(db, tender)
    score = ai_profile_service.get_current(db, subject.id)
    if score is None:
        return []
    if among is not None:
        return [
            e for e in among
            if e.event_type in ANALYSIS_AFFECTING_EVENTS and e.detected_at > score.calculated_at
        ]
    return list(
        db.scalars(
            select(TenderEvent)
            .where(
                TenderEvent.tender_id.in_(_group_ids(db, tender)),
                TenderEvent.event_type.in_(ANALYSIS_AFFECTING_EVENTS),
                TenderEvent.detected_at > score.calculated_at,
            )
            .order_by(TenderEvent.detected_at.desc())
            .limit(50)
        )
    )


# --- новые события для пользователя (зелёная точка) ---------------------------------------------


def _unseen_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=UNSEEN_EVENTS_DAYS)


def unseen_counts(db: Session, user: User) -> dict[uuid.UUID, int]:
    """Сколько событий по каждой закупке пользователь ещё не видел."""

    rows = db.execute(
        select(TenderEvent.tender_id, func.count())
        .outerjoin(
            TenderEventView,
            (TenderEventView.tender_id == TenderEvent.tender_id)
            & (TenderEventView.user_id == user.id),
        )
        .where(
            TenderEvent.created_at >= _unseen_cutoff(),
            (TenderEventView.seen_at.is_(None)) | (TenderEvent.created_at > TenderEventView.seen_at),
        )
        .group_by(TenderEvent.tender_id)
    ).all()
    return {tender_id: count for tender_id, count in rows}


def seen_at(db: Session, user: User, tender_id: uuid.UUID) -> datetime | None:
    view = db.get(TenderEventView, (user.id, tender_id))
    return view.seen_at if view else None


def mark_seen(db: Session, user: User, tender_id: uuid.UUID) -> None:
    view = db.get(TenderEventView, (user.id, tender_id))
    if view is None:
        view = TenderEventView(user_id=user.id, tender_id=tender_id)
    view.seen_at = datetime.now(timezone.utc)
    db.merge(view)
    db.commit()


# --- автоматическое наблюдение за выгруженными в Bitrix24 -------------------------------------


def ensure_for_bitrix(db: Session, tender: Tender, *, actor: User | None = None) -> TenderMonitor | None:
    """Ставит выгруженную в Bitrix24 закупку под наблюдение (раздел 100).

    Исходный слепок — из базы, без заходов на площадку: это ровно то, что только что ушло на
    портал, и выгрузка не должна ждать ЕИС. Снятое человеком наблюдение не включается заново."""

    monitor = get_monitor(db, tender.id)
    if monitor is not None:
        return monitor
    now = datetime.now(timezone.utc)
    snapshot = build_snapshot(db, tender)
    if not snapshot["documents"]:
        snapshot["documents_pending"] = True
    monitor = TenderMonitor(
        tender_id=tender.id,
        enabled=True,
        auto=True,
        started_by_id=actor.id if actor else None,
        snapshot=snapshot,
        snapshot_at=now,
        last_checked_at=now,
        checks_count=0,
    )
    db.add(monitor)
    db.flush()
    return monitor


def _auto_finished(tender: Tender, now: datetime) -> bool:
    if tender.status == "cancelled":
        return True
    deadline = tender.application_end
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline < now - timedelta(days=AUTO_MONITOR_DAYS_AFTER_DEADLINE)


def enroll_bitrix_tenders(db: Session) -> int:
    """Закупки, выгруженные в Bitrix24 до 12.11.2026 или мимо `ensure_for_bitrix`, — под
    наблюдение; отменённые и давно завершённые из автоматического — долой."""

    now = datetime.now(timezone.utc)
    added = 0
    missing = db.scalars(
        select(Tender)
        .join(BitrixDealLink, BitrixDealLink.tender_id == Tender.id)
        .outerjoin(TenderMonitor, TenderMonitor.tender_id == Tender.id)
        .where(BitrixDealLink.deal_id.is_not(None), TenderMonitor.tender_id.is_(None))
    ).all()
    for tender in missing:
        if _auto_finished(tender, now):
            continue
        ensure_for_bitrix(db, tender)
        added += 1
    for monitor in db.scalars(
        select(TenderMonitor).where(TenderMonitor.enabled.is_(True), TenderMonitor.auto.is_(True))
    ).all():
        tender = db.get(Tender, monitor.tender_id)
        if tender is not None and _auto_finished(tender, now):
            monitor.enabled = False
    db.commit()
    return added


def run_all(db: Session, *, delay: float = DELAY_BETWEEN_TENDERS_SECONDS) -> dict[str, int]:
    """Плановый проход: все закупки под наблюдением, затем отправка накопленных событий."""

    from app.services import tender_event_service

    try:
        enroll_bitrix_tenders(db)
    except Exception as exc:  # noqa: BLE001 - без новых закупок проверка остальных всё равно нужна
        db.rollback()
        logger.warning(f"Постановка выгруженных в Bitrix24 закупок под наблюдение не удалась: {exc}")
    monitors = list(db.scalars(select(TenderMonitor).where(TenderMonitor.enabled.is_(True))))
    checked = new_events = failed = 0
    for index, monitor in enumerate(monitors):
        tender = db.get(Tender, monitor.tender_id)
        if tender is None:
            continue
        if index:
            time.sleep(delay)
        try:
            new_events += len(check_tender(db, tender))
            checked += 1
        except Exception as exc:  # noqa: BLE001 - одна закупка не должна останавливать остальные
            db.rollback()
            failed += 1
            logger.warning(f"Проверка закупки {tender.external_id} не удалась: {exc}")
        try:
            tender_event_service.send_pending(db, tender)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning(f"Отправка событий {tender.external_id} не удалась: {exc}")
    return {"checked": checked, "events": new_events, "failed": failed}
