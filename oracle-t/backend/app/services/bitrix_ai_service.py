"""Пакет ИИ-анализа во вкладку сделки Bitrix24 «ИИ анализ и решения» (20.10.2026).

Второй канал рядом с вебхуком (`bitrix_deal_service`, раздел 45 ARCHITECTURE.md). На портале
установлено приложение `/local/tools/tender_ai_analysis/` с обработчиком `receive.php`
(инструкция заказчика «коннект ИИ и Б24», `docs/AI_API.md` на портале, schema_version 2):
один подписанный JSON-пакет на тендер. Портал сам находит сделку по `deal_id` или по
`lookup` (номер процедуры + тип карточки + номер лота), при разрешении администратора
заводит компанию по ИНН и сделку в тестовой воронке и возвращает её id; короткие поля
пишет в карточку, подробные строки раскладывает по шести разделам вкладки.

Что важно по инструкции и почему так сделано:

1. **Подпись.** `hex(HMAC-SHA256(secret, timestamp + "." + request_id + "." + тело))` —
   от тех же байтов, что уходят в запрос, поэтому тело сериализуется один раз и хранится
   (`bitrix_ai_requests.body`). Секрет — только в `bitrix24_settings` (зашифрован), не в
   теле, не в журнале и не в ответах API.
2. **Повтор = те же байты.** Технически не принятый запрос (сеть, 5xx) повторяется с тем же
   `request_id`, `analysis_id` и телом; меняется только время в заголовке. Новый анализ —
   новые `request_id` и `analysis_id`.
3. **Постоянный `key` строки.** Портал обновляет строку по `section + type + key`, а не
   плодит дубль. Требования при повторном разборе документации пересоздаются с новыми id,
   поэтому ключ — хеш от вида, позиции и текста требования, а не id строки.
4. **Только факты и выводы ИИ.** Ни стадии, ни ответственных, ни решений сотрудников в пакете
   нет — их ведёт сам портал. Значения `data` портал показывает как есть, поэтому они
   по-русски; пустые не передаются (инструкция: «лучше не передавать, чем пустую строку»).
5. **Отправка выключена, пока её не включили** (`ai_push_enabled`) — портал боевой.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import SecretStorageError, decrypt_secret, encrypt_secret
from app.models.ai_profile import AiProfileScore
from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceStatus,
    Requirement,
    RequirementKind,
    TenderOutcome,
    WinPercentage,
)
from app.models.bitrix_deal import BitrixAiRequest, BitrixDealLink
from app.models.integration_setting import Bitrix24Settings
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product
from app.models.region import Region
from app.models.tender import Tender, TenderStage, TenderStatus, TenderType
from app.models.tender_card import TenderCard
from app.models.tender_document import TenderDocument
from app.models.user import User
from app.services import tender_positions
from app.services.audit import log_action
from app.services.tender_key_facts import KeyFact, is_generic_norm, key_facts, priority, split_requirement

if TYPE_CHECKING:
    from app.services.ai_conclusion_service import BrandLock

SCHEMA_VERSION = 2
SECTIONS = (
    "preparation",
    "technical_specification",
    "contract",
    "participants_prices",
    "fas",
    "matching",
)
# Пределы портала: 5 МБ на запрос и 5000 строк. Строки режем заранее и с запасом — длинная
# цитата из ТЗ на вкладке всё равно не читается.
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_ITEMS = 5000
MAX_TEXT = 2000
MAX_QUOTE = 600
TIMEOUT_SECONDS = 60.0
_MSK = ZoneInfo("Europe/Moscow")
_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class BitrixAiError(RuntimeError):
    """Отправка невозможна или портал ответил ошибкой. Текст — для человека, без секрета."""


class BitrixAiNotConfiguredError(BitrixAiError):
    """Не задан адрес обработчика или секрет."""


class BitrixAiPushDisabledError(BitrixAiError):
    """Отправка пакетов выключена администратором."""


# --- настройки --------------------------------------------------------------------------------


def _settings(db: Session) -> Bitrix24Settings:
    settings = db.get(Bitrix24Settings, _SINGLETON_ID)
    if settings is None:
        settings = Bitrix24Settings(id=_SINGLETON_ID)
        db.add(settings)
        db.flush()
    return settings


def _secret(settings: Bitrix24Settings) -> str | None:
    if not settings.ai_secret_encrypted:
        return None
    try:
        return decrypt_secret(settings.ai_secret_encrypted)
    except SecretStorageError:
        return None


def normalize_receiver_url(value: str) -> str:
    url = (value or "").strip()
    if not re.match(r"^https://[^/\s]+/\S+\.php$", url):
        raise BitrixAiError(
            "Адрес обработчика должен выглядеть так: https://портал/local/tools/"
            "tender_ai_analysis/integration/receive.php"
        )
    return url


def settings_out(db: Session) -> dict[str, Any]:
    settings = _settings(db)
    secret = _secret(settings)
    return {
        "receiver_url": settings.ai_receiver_url,
        "secret_set": secret is not None,
        # Секрет не отдаётся: только признак и последние четыре знака — сверить с порталом.
        "secret_hint": f"••••{secret[-4:]}" if secret else None,
        "push_enabled": settings.ai_push_enabled,
    }


def update_settings(db: Session, changes: dict[str, Any], *, actor: User) -> dict[str, Any]:
    """PATCH-семантика; пустой адрес или секрет — удаление, и отправка при этом выключается."""

    settings = _settings(db)
    notes: list[str] = []
    if "receiver_url" in changes:
        raw = (changes["receiver_url"] or "").strip()
        settings.ai_receiver_url = normalize_receiver_url(raw) if raw else None
        notes.append(f"обработчик {settings.ai_receiver_url}" if raw else "обработчик удалён")
    if "secret" in changes:
        raw = (changes["secret"] or "").strip()
        if raw and len(raw) < 16:
            raise BitrixAiError("Секрет слишком короткий — скопируйте его из настроек приложения целиком.")
        settings.ai_secret_encrypted = encrypt_secret(raw) if raw else None
        notes.append("секрет задан" if raw else "секрет удалён")
    if not settings.ai_receiver_url or not settings.ai_secret_encrypted:
        settings.ai_push_enabled = False
    if changes.get("push_enabled") is not None:
        enabled = bool(changes["push_enabled"])
        if enabled and (not settings.ai_receiver_url or not settings.ai_secret_encrypted):
            raise BitrixAiNotConfiguredError("Сначала укажите адрес обработчика и секрет.")
        settings.ai_push_enabled = enabled
        notes.append("отправка пакетов включена" if enabled else "отправка пакетов выключена")
    settings.updated_by_id = actor.id
    log_action(
        db,
        component="integrations",
        action="update_bitrix24_ai_settings",
        result="success",
        details=", ".join(notes) or "без изменений",
        user_id=actor.id,
    )
    db.commit()
    return settings_out(db)


# --- значения ---------------------------------------------------------------------------------


def _clip(text: str | None, limit: int = MAX_TEXT, *, lines: bool = False) -> str | None:
    # `lines` — переводы строк сохраняются: причины несоответствия в выжимке портал ждёт
    # каждую с новой строки.
    if text is None:
        return None
    if lines:
        text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in str(text).splitlines()).strip()
    else:
        text = re.sub(r"\s+", " ", str(text)).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_MSK).isoformat(timespec="seconds")
    # Дата без времени — полночь по Москве: портал требует ISO 8601 с часовым поясом.
    return datetime(value.year, value.month, value.day, tzinfo=_MSK).isoformat(timespec="seconds")


def _number(value: Decimal | float | None) -> float | int | None:
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else round(number, 2)


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _key(prefix: str, *parts: Any) -> str:
    raw = "|".join(re.sub(r"\s+", " ", str(part or "")).strip().lower() for part in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


_CRITICALITY = {"critical": "Критическое", "important": "Важное", "minor": "Второстепенное"}
_COMPLIANCE = {
    "meets": "Соответствует",
    "partial": "Соответствует частично",
    "not_meets": "Не соответствует",
    "no_data": "Нет данных — нужно проверить",
}
_VERDICT = {
    "passes": "Проходит по ТЗ",
    "caveats": "Проходит с оговорками",
    "fails": "Не проходит по ТЗ",
    "unknown": "Не хватает данных",
}
_SEVERITY = {"significant": "Существенный", "moderate": "Умеренный", "minor": "Незначительный"}
# `status` строки — вывод ИИ, по нему портал рисует плашку («Нужно проверить»). Значения — как
# в примерах `docs/AI_API.md` 1.4.1: `extracted`, `matches`, `partially_matches`,
# `requires_review`; `not_matches` — по аналогии (в примерах портала не встречается).
STATUS_EXTRACTED = "extracted"
STATUS_REVIEW = "requires_review"
_ROW_STATUS = {
    "meets": "matches",
    "partial": "partially_matches",
    "not_meets": "not_matches",
    "no_data": STATUS_REVIEW,
}
_VERDICT_STATUS = {
    "passes": "matches",
    "caveats": "partially_matches",
    "fails": "not_matches",
    "unknown": STATUS_REVIEW,
}
_PRODUCT_STATUS = {
    "fits": "matches",
    "partial": "partially_matches",
    "not_fits": "not_matches",
    "unchecked": STATUS_REVIEW,
}
# Риски заключения про договор и исполнение — в раздел «Договор», остальные — в подготовку.
_CONTRACT_RISKS = {"contract", "price", "deadline", "logistics", "customer"}


def _status_rank(status: str) -> int:
    return {"meets": 0, "partial": 1, "no_data": 2, "not_meets": 3}.get(status, 4)


# --- короткие поля и поиск сделки ---------------------------------------------------------------


def procedure_number(tender: Tender) -> str:
    return (tender.registry_number or tender.external_id or "").strip()


def customer_inn(db: Session, tender: Tender) -> str | None:
    """ИНН заказчика из карточки закупки (ЕИС кладёт его в «Сведения о заказчике»).

    В модели тендера ИНН нет, а портал ищет по нему компанию; сначала — раздел про
    заказчика, затем любой ИНН карточки. Только 10 или 12 цифр — иначе портал отклонит пакет."""

    card = db.get(TenderCard, tender.id)
    if card is None:
        return None
    candidates: list[tuple[int, str]] = []
    for section in (card.payload or {}).get("sections", []) or []:
        title = str(section.get("title") or "").lower()
        for field in section.get("fields", []) or []:
            if not isinstance(field, (list, tuple)) or len(field) < 2:
                continue
            if str(field[0]).strip().upper() != "ИНН":
                continue
            digits = re.sub(r"\D", "", str(field[1]))
            if len(digits) in (10, 12):
                candidates.append((0 if "заказчик" in title else 1, digits))
    return min(candidates)[1] if candidates else None


def _direction(tender: Tender) -> str:
    text = (tender.title or "").lower()
    if re.search(r"\bгаз", text):
        return "GAS"
    if re.search(r"тепл", text):
        return "HEAT"
    if re.search(r"\bвод[аоыуя]|водоснаб|водомер|\bгвс\b|\bхвс\b", text):
        return "WATER"
    # Платформа отбирает закупки по учёту электроэнергии — это направление по умолчанию.
    return "ELECTRICITY"


def _tender_types(tender: Tender) -> list[str]:
    text = (tender.title or "").lower()
    types: list[str] = []
    mapping = {
        TenderType.SUPPLY_ONLY.value: "IPU",
        TenderType.COMPLEX.value: "COMPLEX",
        TenderType.WORKS_ONLY.value: "WORKS",
        TenderType.REVERIFICATION.value: "VERIFY",
    }
    if tender.tender_type in mapping:
        types.append(mapping[tender.tender_type])
    if "успд" in text:
        types.append("USPD")
    if re.search(r"аиис|аскуэ|асуэ|аис куэ", text):
        types.append("ASKUE")
    if re.search(r"программн|\bпо\b", text):
        types.append("SOFTWARE")
    if not types and re.search(r"счётчик|счетчик|прибор", text):
        types.append("IPU")
    return list(dict.fromkeys(types))


def _result_status(db: Session, tender: Tender) -> str:
    if tender.status == TenderStatus.CANCELLED.value:
        return "CANCELLED"
    outcome = db.scalar(
        select(TenderOutcome)
        .where(TenderOutcome.tender_id == tender.id)
        .order_by(TenderOutcome.created_at.desc())
    )
    if outcome is not None and outcome.winner_manufacturer_name:
        mirtek = db.scalars(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True))).all()
        names = {
            (name or "").lower()
            for item in mirtek
            for name in (item.brand_name, item.legal_name)
            if name
        }
        winner = outcome.winner_manufacturer_name.lower()
        return "WIN" if any(name in winner or winner in name for name in names) else "LOSS"
    return "PENDING"


def build_lookup(db: Session, tender: Tender) -> dict[str, Any]:
    return _compact(
        {
            "procedure_number": procedure_number(tender),
            "lot_number": "1",
            "customer_inn": customer_inn(db, tender),
            "customer_name": _clip(tender.customer_name, 500),
            "procedure_url": tender.source_url,
        }
    )


# Разбор дат процедуры из карточки ЕИС вынесен в общий модуль 20.10.2026: теми же
# значениями карточка уточняет сроки подачи у самого тендера (раздел 68 ARCHITECTURE.md).
from app.services.eis_card_dates import card_dates, parse_card_datetime  # noqa: E402,F401


def build_deal_fields(db: Session, tender: Tender) -> dict[str, Any]:
    region = db.get(Region, tender.region_delivery_code) if tender.region_delivery_code else None
    dates = card_dates(db, tender)
    subject = _clip(tender.title, 180) or "Закупка"
    return _compact(
        {
            "title": f"{procedure_number(tender)} — {subject} — лот 1"[:250],
            "amount": _number(tender.price),
            "currency": tender.currency or "RUB",
            "procedure_url": tender.source_url,
            "direction": _direction(tender),
            "tender_types": _tender_types(tender),
            "delivery_region": region.name if region else None,
            "procurement_method": _clip(tender.procurement_method, 255),
            # Карточка точнее: окончание подачи в модели — дата из списка выдачи ЕИС без времени.
            "application_start": _iso(dates.get("application_start") or tender.application_start),
            "application_end": _iso(dates.get("application_end") or tender.application_end),
            "result_date": _iso(dates.get("result_date")),
            "auction_date": _iso(dates.get("auction_date")),
            "rebid_date": _iso(dates.get("rebid_date")),
            # Закупка у нас — одна карточка на процедуру: лоты отдельно не ведутся.
            "lot_number": "1",
            "record_type": "SINGLE",
            "result_status": _result_status(db, tender),
        }
    )


# --- подробные строки ---------------------------------------------------------------------------


def _documents(db: Session, tender: Tender) -> dict[uuid.UUID, str]:
    return {
        doc.id: doc.file_name
        for doc in db.scalars(select(TenderDocument).where(TenderDocument.tender_id == tender.id))
    }


def _evidence(document: str | None, quote: str | None) -> list[dict[str, Any]]:
    item = _compact({"document": document, "quote": _clip(quote, MAX_QUOTE)})
    return [item] if item else []


def _row(
    key: str,
    type_: str,
    title: str,
    data: dict[str, Any],
    *,
    evidence: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    updated_at: datetime | None = None,
    status: str = STATUS_EXTRACTED,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "type": type_,
        "title": _clip(title, 250) or "—",
        "status": status,
        "data": _compact(data),
        "evidence": evidence or [],
    }
    if confidence is not None:
        row["confidence"] = round(max(0.0, min(1.0, float(confidence))), 3)
    if updated_at is not None:
        row["source_updated_at"] = _iso(updated_at)
    return row


def _requirement_title(requirement: Requirement) -> str:
    # Полный текст — в `data.requirement`; заголовок строки во вкладке короткий.
    return _clip(requirement.field_name or requirement.category or requirement.text, 120) or "Требование"


def _mirtek_cells(db: Session, tender: Tender) -> dict[uuid.UUID, tuple[ComplianceMatrixEntry, str | None]]:
    """Лучшая ячейка матрицы МИРТЕК по каждому требованию и модель, к которой она относится."""

    rows = db.execute(
        select(ComplianceMatrixEntry, Product.model_name)
        .join(Manufacturer, Manufacturer.id == ComplianceMatrixEntry.manufacturer_id)
        .outerjoin(Product, Product.id == ComplianceMatrixEntry.product_id)
        .where(ComplianceMatrixEntry.tender_id == tender.id, Manufacturer.is_mirtek.is_(True))
    ).all()
    best: dict[uuid.UUID, tuple[ComplianceMatrixEntry, str | None]] = {}
    for entry, model in rows:
        current = best.get(entry.requirement_id)
        if current is None or _status_rank(entry.status) < _status_rank(current[0].status):
            best[entry.requirement_id] = (entry, model)
    return best


def build_sections(db: Session, tender: Tender) -> dict[str, list[dict[str, Any]]]:
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTIONS}
    documents = _documents(db, tender)
    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender.id)
            .order_by(Requirement.created_at, Requirement.parameter_no)
        )
    )
    cells = _mirtek_cells(db, tender)
    lock = _brand_lock(db, tender, requirements)

    # Замечания тендерного отдела 23.10.2026: менеджер смотрит сначала ключевые сведения и
    # причины несоответствия, а не сотню требований подряд. Сведения — блоком «Краткая
    # выжимка по ТЗ» (одна строка `technical_summary`), первым в разделе.
    summary_row = _summary_row(key_facts(db, tender, requirements, cells, lock))

    # ТЗ и номенклатура: требования к товару и работам — с выводом по МИРТЕК, по приоритету:
    # сначала то, по чему наш прибор не проходит, затем критичные. Общие нормы (паспорт,
    # методика поверки, условия поставки, типовые требования к участнику по закону) не
    # передаются, если только прибор по ним не проходит. Требования к участнику — квалификация
    # в «Подготовке заявки».
    requirement_rows: list[tuple[tuple[int, int, int], int, dict[str, Any]]] = []
    for index, requirement in enumerate(requirements):
        cell = cells.get(requirement.id)
        entry, model = cell if cell else (None, None)
        if is_generic_norm(requirement) and not (entry and entry.status == ComplianceStatus.NOT_MEETS.value):
            continue
        document = documents.get(requirement.source_document_id) if requirement.source_document_id else None
        evidence = _evidence(document, requirement.text)
        criticality = _CRITICALITY.get(requirement.criticality)
        key = _key("req", requirement.kind, requirement.position, requirement.normalized_text or requirement.text)
        if requirement.kind == RequirementKind.PARTICIPANT.value:
            sections["preparation"].append(
                _row(
                    key,
                    "qualification",
                    _requirement_title(requirement),
                    {
                        "requirement": _clip(requirement.text),
                        "criticality": criticality,
                        "blocking": "Да" if requirement.criticality == "critical" else None,
                        "result": "Нужно проверить подтверждающие документы",
                    },
                    evidence=evidence,
                    updated_at=requirement.created_at,
                    status=STATUS_REVIEW,
                )
            )
            continue
        # Портал 1.4.1 показывает параметр и требуемое значение раздельно (`required_value`);
        # формулировка ТЗ целиком — в цитате источника.
        subject, required = split_requirement(requirement)
        row = _row(
            key,
            "requirement",
            _requirement_title(requirement),
            {
                "position": _clip(requirement.position, 200),
                "requirement": _clip(subject),
                "required_value": _clip(required),
                "criticality": criticality,
                "equivalent_allowed": "Нет" if requirement.vendor_exclusive else None,
                "manufacturer": requirement.vendor,
                "proposed_model": model,
                "result": _COMPLIANCE.get(entry.status) if entry else None,
                "actual_value": _clip(entry.explanation) if entry else None,
            },
            evidence=evidence,
            confidence=float(entry.confidence) if entry and entry.confidence is not None else None,
            updated_at=requirement.created_at,
            status=_ROW_STATUS.get(entry.status, STATUS_REVIEW) if entry else STATUS_REVIEW,
        )
        requirement_rows.append((priority(requirement, entry.status if entry else None), index, row))
    requirement_rows.sort(key=lambda item: item[:2])

    score = db.scalar(
        select(AiProfileScore)
        .where(AiProfileScore.tender_id == tender.id, AiProfileScore.is_current.is_(True))
        .order_by(AiProfileScore.calculated_at.desc())
    )
    conclusion = (score.conclusion if score else None) or {}

    # Вывод по производителям: процент и вердикт матрицы плюс комментарий заключения ИИ.
    manufacturers = {item.id: item for item in db.scalars(select(Manufacturer))}
    notes_by_name = {
        (item.get("manufacturer") or "").lower(): item for item in conclusion.get("competitors") or []
    }
    wins = db.scalars(
        select(WinPercentage).where(
            WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True)
        )
    ).all()
    competitor_rows: list[dict[str, Any]] = []
    for win in sorted(wins, key=lambda item: -float(item.percentage)):
        manufacturer = manufacturers.get(win.manufacturer_id)
        if manufacturer is None:
            continue
        name = manufacturer.brand_name or manufacturer.legal_name
        if manufacturer.is_mirtek:
            continue
        note = notes_by_name.get(name.lower())
        competitor_rows.append(
            _row(
                f"mfr-{manufacturer.id}",
                "manufacturer_match",
                name,
                {
                    "manufacturer": name,
                    "model": (note or {}).get("model"),
                    "match_percent": _number(win.percentage),
                    "result": _VERDICT.get(win.verdict or "", None),
                    "risk": _clip((note or {}).get("note")) or _clip(win.reason_summary),
                },
                updated_at=win.calculated_at,
                status=_VERDICT_STATUS.get(win.verdict or "", STATUS_REVIEW),
            )
        )
    mirtek_win = next(
        (win for win in wins if manufacturers.get(win.manufacturer_id) and manufacturers[win.manufacturer_id].is_mirtek),
        None,
    )
    mirtek_rows: list[dict[str, Any]] = []
    for product in conclusion.get("our_products") or []:
        mirtek_rows.append(
            _row(
                f"mirtek-{product.get('product_id') or _key('m', product.get('model'))}",
                "mirtek_recommendation",
                product.get("model") or "Прибор МИРТЕК",
                {
                    "proposed_model": product.get("model"),
                    "match_percent": _number(mirtek_win.percentage) if mirtek_win else None,
                    "result": product.get("status_label"),
                    "action": _clip(product.get("note")),
                },
                updated_at=score.calculated_at if score else None,
                status=_PRODUCT_STATUS.get(product.get("status") or "", STATUS_REVIEW),
            )
        )
    if not conclusion.get("our_products") and mirtek_win is not None:
        mirtek_rows.append(
            _row(
                "mirtek-matrix",
                "mirtek_recommendation",
                "МИРТЕК по матрице соответствия",
                {
                    "match_percent": _number(mirtek_win.percentage),
                    "result": _VERDICT.get(mirtek_win.verdict or "", None),
                    "risk": _clip(mirtek_win.reason_summary),
                },
                updated_at=mirtek_win.calculated_at,
                status=_VERDICT_STATUS.get(mirtek_win.verdict or "", STATUS_REVIEW),
            )
        )

    # Порядок вкладки: выжимка, позиции закупки, наши приборы, требования по приоритету,
    # конкуренты.
    sections["technical_specification"] = (
        [summary_row] + position_rows(db, tender) + mirtek_rows + [row for *_, row in requirement_rows] + competitor_rows
    )

    # Подготовка заявки: вывод ИИ об участии и шаги стратегии — как пункты чек-листа.
    if conclusion.get("fit_label"):
        sections["preparation"].append(
            _row(
                "ai-conclusion",
                "checklist",
                f"Вывод ИИ: {conclusion['fit_label']}",
                {
                    "result": _clip(conclusion.get("headline")),
                    "influence": _clip(conclusion.get("rationale")),
                },
                updated_at=score.calculated_at if score else None,
            )
        )
    strategy = conclusion.get("strategy") or {}
    for index, step in enumerate(strategy.get("steps") or [], start=1):
        sections["preparation"].append(
            _row(f"strategy-step-{index}", "checklist", f"Шаг {index}", {"action": _clip(step)})
        )
    for risk in conclusion.get("risks") or []:
        contract = risk.get("category") in _CONTRACT_RISKS
        sections["contract" if contract else "preparation"].append(
            _row(
                _key("risk", risk.get("category"), risk.get("text")),
                "contract_risk" if contract else "checklist",
                f"Риск: {risk.get('category_label') or 'прочее'}",
                {
                    "risk": _clip(risk.get("text")),
                    "criticality": _SEVERITY.get(risk.get("severity") or ""),
                    "measure": _clip(risk.get("mitigation")),
                },
                updated_at=score.calculated_at if score else None,
            )
        )

    # Условия из вкладки «Дополнительно»: договор, особые условия, требования к заявке.
    card = db.get(TenderCard, tender.id)
    extra = ((card.payload or {}).get("extra_sections") or {}) if card else {}
    extra_sections = extra.get("sections") or {}
    extra_at = None
    if extra.get("generated_at"):
        try:
            extra_at = datetime.fromisoformat(extra["generated_at"])
        except ValueError:
            extra_at = None
    for source_key, section, type_, field, title in (
        ("contract_terms", "contract", "contract_condition", "requirement", "Условие договора"),
        ("special_conditions", "contract", "obligation", "requirement", "Обязательство"),
        ("application_requirements", "preparation", "checklist", "document", "Состав заявки"),
    ):
        for text in extra_sections.get(source_key) or []:
            sections[section].append(
                _row(
                    _key(source_key, text),
                    type_,
                    _clip(text, 120) or title,
                    {field: _clip(text)},
                    updated_at=extra_at,
                )
            )

    # Участники и цены: итог процедуры из протокола.
    outcome = db.scalar(
        select(TenderOutcome)
        .where(TenderOutcome.tender_id == tender.id)
        .order_by(TenderOutcome.created_at.desc())
    )
    if outcome is not None and (outcome.winner_manufacturer_name or outcome.winner_price is not None):
        deviation = None
        if outcome.winner_price is not None and tender.price:
            deviation = round((float(outcome.winner_price) / float(tender.price) - 1) * 100, 2)
        sections["participants_prices"].append(
            _row(
                "procedure-result",
                "procedure_result",
                "Итог процедуры",
                {
                    "winner": outcome.winner_manufacturer_name,
                    "initial_price": _number(tender.price),
                    "final_price": _number(outcome.winner_price),
                    "deviation_percent": deviation,
                    "date": _iso(outcome.published_at),
                    "document": outcome.protocol_url,
                },
            )
        )

    # ФАС: ТЗ под товарный знак конкурента без эквивалента — признак ограничения конкуренции.
    _brand_lock_signal(lock, sections)

    # Порядок строк внутри раздела — как собраны; общий предел портала — 5000 строк.
    total = 0
    for name in SECTIONS:
        # Портал отклоняет пакет целиком, если `type + key` в разделе повторяется (первая
        # выгрузка 20.10.2026, HTTP 400): модель извлечения иногда даёт два требования,
        # различающихся одним двоеточием, а ключ строится от нормализованного текста.
        # Повтор получает порядковый суффикс — порядок требований стабилен (по времени и номеру).
        seen: dict[tuple[str, str], int] = {}
        for row in sections[name]:
            marker = (row["type"], row["key"])
            seen[marker] = seen.get(marker, 0) + 1
            if seen[marker] > 1:
                row["key"] = f"{row['key']}-{seen[marker]}"
        rows = sections[name][: max(0, MAX_ITEMS - total)]
        for index, row in enumerate(rows, start=1):
            row["sort"] = index * 10
        sections[name] = rows
        total += len(rows)
    return sections


def _brand_lock(db: Session, tender: Tender, requirements: list[Requirement]) -> "BrandLock | None":
    """Эталон ТЗ по требованиям к товару: нужен и сигналу ФАС, и ключевым сведениям."""

    # Импорт здесь: модуль заключения тяжёлый (клиенты моделей), а нужен ему один детектор.
    from app.services.ai_conclusion_service import detect_brand_lock

    product = [item for item in requirements if item.kind == RequirementKind.PRODUCT.value]
    if not product:
        return None
    return detect_brand_lock(db, tender, product, list(db.scalars(select(Manufacturer))))


def position_rows(db: Session, tender: Tender) -> list[dict[str, Any]]:
    """Позиции закупки строками `procurement_position` (портал 1.4.1). Ключ постоянный —
    по номеру лота и позиции, без номера — по наименованию: позиции пересоздаются при каждом
    разборе, и ключ от id завёл бы на портале дубли."""

    rows = []
    for item in tender_positions.list_positions(db, tender):
        number = (item.position_no or "").strip()
        lot = re.sub(r"[^\w.-]+", "-", item.lot_number or "1").strip("-") or "1"
        row_no = re.sub(r"[^\w.-]+", "-", number).strip("-")
        if row_no:
            key = f"procurement-position-lot-{lot}-row-{row_no}"
        else:
            key = f"procurement-position-lot-{lot}-{_key('name', item.name)}"
        quantity = _number(item.quantity) if item.quantity is not None else _clip(item.quantity_text, 100)
        rows.append(
            _row(
                key,
                "procurement_position",
                f"Позиция №{number}" if number else (_clip(item.name, 120) or "Позиция"),
                {
                    "position": number or None,
                    "name": _clip(item.name, 1000),
                    "unit": item.unit,
                    "quantity": quantity,
                    "lot_number": item.lot_number or "1",
                },
                evidence=_evidence(item.document_name, item.quote),
            )
        )
    return rows


def _summary_row(facts: list[KeyFact]) -> dict[str, Any]:
    """Строка «Краткая выжимка по ТЗ» (`technical_summary`, портал 1.4.1): одна на сделку,
    ключ постоянный, имена полей `data` — как в документации портала. Статус «нужно
    проверить», если хоть одно сведение выведено по признакам или не найдено."""

    evidence = [
        item
        for fact in facts
        if fact.quote
        for item in _evidence(fact.document, f"{fact.title}: {fact.quote}")
    ]
    return _row(
        "technical-summary",
        "technical_summary",
        "Краткая выжимка по ТЗ",
        {fact.key: _clip(fact.text, lines=True) for fact in facts},
        evidence=evidence,
        status=STATUS_REVIEW if any(fact.review for fact in facts) else STATUS_EXTRACTED,
    )


def _brand_lock_signal(lock: "BrandLock | None", sections: dict[str, list]) -> None:
    if lock is None or lock.is_ours:
        return
    if lock.equivalent_allowed is not False and not lock.exclusive:
        return
    name = lock.manufacturer.brand_name or lock.manufacturer.legal_name
    reason = (
        "эквивалент прямо запрещён" if lock.equivalent_allowed is False else
        "требуется фирменное ПО или сервис производителя: " + ", ".join(lock.exclusive)
    )
    sections["fas"].append(
        _row(
            f"brand-lock-{lock.manufacturer.id}",
            "fas_signal",
            f"ТЗ под товарный знак {name}",
            {
                "requirement": "Модели: " + ", ".join(lock.models) if lock.models else None,
                "reason": f"ТЗ называет модели одного производителя, {reason}",
                "risk": "Возможное ограничение конкуренции",
                "question": "Допускается ли эквивалент указанной модели?",
            },
            evidence=_evidence(None, lock.clause),
            status=STATUS_REVIEW,
        )
    )


# --- пакет и отправка ----------------------------------------------------------------------------


def _deal_id(db: Session, tender: Tender) -> int:
    link = db.get(BitrixDealLink, tender.id)
    return int(link.deal_id) if link and link.deal_id else 0


def build_payload(
    db: Session, tender: Tender, *, full_snapshot: bool = False, request_id: str | None = None
) -> dict[str, Any]:
    sections = build_sections(db, tender)
    now = datetime.now(timezone.utc)
    digest = hashlib.sha1(
        json.dumps(sections, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"sova-{uuid.uuid4()}",
        "deal_id": _deal_id(db, tender),
        # Новая версия анализа на каждую отправку: портал ведёт по ней историю строк.
        "analysis_id": f"sova-{tender.id.hex[:12]}-{now:%Y%m%dT%H%M%SZ}-{digest}-{uuid.uuid4().hex[:6]}",
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "full_snapshot": bool(full_snapshot),
        "lookup": build_lookup(db, tender),
        "deal_fields": build_deal_fields(db, tender),
        "sections": sections,
    }


def serialize(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(secret: str, timestamp: str, request_id: str, body: bytes) -> str:
    message = timestamp.encode("utf-8") + b"." + request_id.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def items_count(payload: dict[str, Any]) -> int:
    return sum(len(rows) for rows in (payload.get("sections") or {}).values())


def resolve_tender(db: Session, ref: str) -> Tender:
    """Тендер по id или по номеру закупки — администратор вводит то, что видит в списке."""

    ref = (ref or "").strip()
    try:
        tender = db.get(Tender, uuid.UUID(ref))
    except ValueError:
        tender = db.scalar(
            select(Tender)
            .where((Tender.external_id == ref) | (Tender.registry_number == ref))
            .order_by(Tender.created_at.desc())
        )
    if tender is None:
        raise LookupError(f"Тендер «{ref}» не найден")
    return tender


def preview(db: Session, tender: Tender, *, full_snapshot: bool = False) -> dict[str, Any]:
    payload = build_payload(db, tender, full_snapshot=full_snapshot)
    return {
        "tender_id": str(tender.id),
        "payload": payload,
        "items_count": items_count(payload),
        "size_bytes": len(serialize(payload)),
    }


def _post(settings: Bitrix24Settings, record: BitrixAiRequest, transport: httpx.BaseTransport | None) -> None:
    secret = _secret(settings)
    if not settings.ai_receiver_url or not secret:
        raise BitrixAiNotConfiguredError(
            "Обработчик Bitrix24 не настроен: укажите адрес receive.php и секрет в «Приложения → Bitrix24»."
        )
    body = record.body.encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Tender-AI-Timestamp": timestamp,
        "X-Tender-AI-Request-Id": record.request_id,
        "X-Tender-AI-Signature": sign(secret, timestamp, record.request_id, body),
    }
    record.attempts = (record.attempts or 0) + 1
    record.sent_at = datetime.now(timezone.utc)
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(settings.ai_receiver_url, content=body, headers=headers)
    except httpx.TimeoutException:
        record.http_status = None
        record.error = f"Портал не ответил за {TIMEOUT_SECONDS:.0f} с — пакет можно повторить."
        return
    except httpx.HTTPError as exc:
        record.http_status = None
        record.error = f"Не удалось связаться с порталом: {type(exc).__name__} — пакет можно повторить."
        return

    record.http_status = response.status_code
    record.response_body = response.text[:20000]
    try:
        answer = response.json()
    except ValueError:
        answer = None
    result = (answer or {}).get("result") if isinstance(answer, dict) else None
    if response.status_code in (200, 202) and isinstance(answer, dict) and answer.get("ok"):
        result = result or {}
        record.accepted = True
        record.duplicate = bool(result.get("duplicate"))
        record.deal_id = int(result["deal_id"]) if result.get("deal_id") else None
        record.deal_created = result.get("deal_created")
        record.error = None
        return
    record.accepted = False
    record.error = _describe(response.status_code, answer)


_HTTP_HINTS = {
    400: "ошибка структуры JSON, даты, значения или идентификатора",
    401: "не приняты подпись, заголовки или время запроса — сверьте секрет и часы сервера",
    403: "сделка не относится к тестовой тендерной воронке",
    404: "сделка не найдена",
    409: "конфликт: повтор запроса с другим телом, другая компания у сделки или блокировка создания",
    413: "пакет больше 5 МБ",
    422: "недостаточно данных для поиска или создания сделки либо компании",
    500: "внутренняя ошибка портала — пакет можно повторить",
    503: "на портале не задан секрет интеграции",
}


def _describe(status_code: int, answer: Any) -> str:
    detail = ""
    if isinstance(answer, dict):
        error = answer.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        elif error:
            detail = str(error)
        detail = detail or str(answer.get("message") or "")
    hint = _HTTP_HINTS.get(status_code, "портал не принял пакет")
    return f"HTTP {status_code}: {hint}" + (f". Ответ портала: {detail}" if detail else "")


def _finish(db: Session, tender: Tender, record: BitrixAiRequest, actor: User) -> dict[str, Any]:
    if record.accepted and record.deal_id:
        link = db.get(BitrixDealLink, tender.id) or BitrixDealLink(tender_id=tender.id)
        link.deal_id = record.deal_id
        link.pushed_at = record.sent_at
        link.pushed_by_id = actor.id
        link.last_error = None
        db.merge(link)
    log_action(
        db,
        component="integrations",
        action="push_bitrix24_ai_package",
        result="success" if record.accepted else "error",
        level=LogLevel.INFO if record.accepted else LogLevel.WARNING,
        details=(
            f"Тендер {tender.external_id}: request_id {record.request_id}, "
            + (
                f"сделка {record.deal_id}{' создана' if record.deal_created else ''}, строк {record.items_count}"
                + (" (повтор, уже был принят)" if record.duplicate else "")
                if record.accepted
                else record.error or "ошибка"
            )
        ),
        user_id=actor.id,
    )
    db.commit()
    db.refresh(record)
    return request_out(db, record)


def send(
    db: Session,
    tender: Tender,
    *,
    actor: User,
    full_snapshot: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    settings = _settings(db)
    if not settings.ai_push_enabled:
        raise BitrixAiPushDisabledError(
            "Отправка пакетов в Bitrix24 выключена. Включите её в «Приложения → Bitrix24» после "
            "проверки адреса и секрета."
        )
    payload = build_payload(db, tender, full_snapshot=full_snapshot)
    body = serialize(payload)
    if len(body) > MAX_BODY_BYTES:
        raise BitrixAiError(f"Пакет {len(body) // 1024} КБ больше предела портала 5 МБ.")
    record = BitrixAiRequest(
        tender_id=tender.id,
        request_id=payload["request_id"],
        analysis_id=payload["analysis_id"],
        deal_id_sent=payload["deal_id"],
        full_snapshot=payload["full_snapshot"],
        items_count=items_count(payload),
        body=body.decode("utf-8"),
        sent_by_id=actor.id,
    )
    db.add(record)
    _post(settings, record, transport)
    return _finish(db, tender, record, actor)


def retry(
    db: Session, request_pk: uuid.UUID, *, actor: User, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Повтор тем же `request_id` и теми же байтами — только для технически не принятого
    запроса (нет ответа или 5xx). Ошибку данных (4xx) повтор не исправит: нужен новый пакет."""

    record = db.get(BitrixAiRequest, request_pk)
    if record is None:
        raise LookupError("Запрос не найден")
    if record.accepted:
        raise BitrixAiError("Этот пакет уже принят порталом — повторять незачем.")
    if record.http_status is not None and record.http_status < 500:
        raise BitrixAiError(
            f"Портал отклонил пакет (HTTP {record.http_status}) — повтор того же тела не поможет, "
            "отправьте новый пакет."
        )
    settings = _settings(db)
    if not settings.ai_push_enabled:
        raise BitrixAiPushDisabledError("Отправка пакетов в Bitrix24 выключена.")
    tender = db.get(Tender, record.tender_id)
    _post(settings, record, transport)
    return _finish(db, tender, record, actor)


def request_out(db: Session, record: BitrixAiRequest) -> dict[str, Any]:
    tender = db.get(Tender, record.tender_id)
    settings = db.get(Bitrix24Settings, _SINGLETON_ID)
    portal = None
    if settings and settings.ai_receiver_url:
        match = re.match(r"^(https://[^/]+)", settings.ai_receiver_url)
        portal = match.group(1) if match else None
    return {
        "id": str(record.id),
        "tender_id": str(record.tender_id),
        "tender_number": tender.external_id if tender else None,
        "tender_title": tender.title if tender else None,
        "request_id": record.request_id,
        "analysis_id": record.analysis_id,
        "deal_id_sent": record.deal_id_sent,
        "full_snapshot": record.full_snapshot,
        "items_count": record.items_count,
        "http_status": record.http_status,
        "response_body": record.response_body,
        "accepted": record.accepted,
        "duplicate": record.duplicate,
        "deal_id": record.deal_id,
        "deal_created": record.deal_created,
        "deal_url": f"{portal}/crm/deal/details/{record.deal_id}/" if portal and record.deal_id else None,
        "error": record.error,
        "attempts": record.attempts,
        "retryable": not record.accepted and (record.http_status is None or record.http_status >= 500),
        "sent_at": record.sent_at,
    }


def list_requests(db: Session, *, limit: int = 30) -> list[dict[str, Any]]:
    records = db.scalars(
        select(BitrixAiRequest)
        .order_by(BitrixAiRequest.sent_at.desc().nullslast(), BitrixAiRequest.created_at.desc())
        .limit(limit)
    ).all()
    return [request_out(db, record) for record in records]


# --- кнопка «Загрузить в Битрикс» на странице тендера (20.10.2026) -------------------------------

# Выгружается проверенная закупка: человек взял её в работу («На проверке») или дальше.
# «Новая» — ещё никто не смотрел, «Отклонён» — работы по ней не будет; сделка в воронке
# под такую закупку только засоряла бы канбан менеджеров.
ELIGIBLE_STAGES = {
    TenderStage.UNDER_REVIEW.value,
    TenderStage.APPLICATION_SUBMITTED.value,
    TenderStage.WON.value,
    TenderStage.LOST.value,
}


def _not_eligible_reason(tender: Tender) -> str | None:
    if tender.stage in ELIGIBLE_STAGES:
        return None
    if tender.stage == TenderStage.REJECTED.value:
        return "Закупка отклонена — в Bitrix24 её не выгружаем."
    return "Сначала проверьте закупку: переведите её на этап «На проверке»."


def tender_state(db: Session, tender: Tender) -> dict[str, Any]:
    """Что показывать у кнопки: можно ли выгрузить, куда уже выгружено, чем кончилась
    последняя попытка."""

    settings = _settings(db)
    last = db.scalar(
        select(BitrixAiRequest)
        .where(BitrixAiRequest.tender_id == tender.id)
        .order_by(BitrixAiRequest.sent_at.desc().nullslast(), BitrixAiRequest.created_at.desc())
    )
    last_out = request_out(db, last) if last else None
    deal_id = _deal_id(db, tender) or None
    portal = None
    if settings.ai_receiver_url:
        match = re.match(r"^(https://[^/]+)", settings.ai_receiver_url)
        portal = match.group(1) if match else None
    reason = _not_eligible_reason(tender)
    if reason is None and not (settings.ai_receiver_url and settings.ai_secret_encrypted):
        reason = "Bitrix24 не подключён — обратитесь к администратору."
    elif reason is None and not settings.ai_push_enabled:
        # Название флажка и путь к нему (24.10.2026): «выключена администратором» читали как
        # поломку — Bitrix24 на странице выглядит подключённым, а снят лишь флажок отправки.
        reason = (
            "Отправка в Bitrix24 выключена: не стоит флажок «Отправлять пакеты анализа»."
        )
    return {
        "can_upload": reason is None,
        "reason": reason,
        "deal_id": deal_id,
        "deal_url": f"{portal}/crm/deal/details/{deal_id}/" if portal and deal_id else None,
        "last_request": last_out,
    }


def upload_from_card(
    db: Session, tender: Tender, *, actor: User, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    reason = _not_eligible_reason(tender)
    if reason:
        raise BitrixAiError(reason)
    # Полный снимок (23.10.2026): пакет содержит весь анализ, и строки прошлых выгрузок,
    # которых в нём больше нет (общие нормы, пересозданные требования), портал скрывает как
    # неактуальные, а не держит рядом с новыми. Решения сотрудников портал хранит отдельно —
    # их снимок не трогает (`docs/AI_API.md` 1.4.1).
    send(db, tender, actor=actor, transport=transport, full_snapshot=True)
    return tender_state(db, tender)
