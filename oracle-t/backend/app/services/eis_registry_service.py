"""Сведения реестров ЕИС по закупке: жалобы, договоры, история заказчика (19.11.2026, раздел 115).

Пункты перечня Bitrix24 «Что еще нужно от ИИ», которые упирались в источники, а не в портал:
пп. 41–47, 89 (жалобы и решения ФАС), 90, 94 (окончательный договор и допсоглашения), 7 (закупки
заказчика по ОКПД2 за год). Адаптер — `adapters/eis_registries.py`, хранение —
`tender_eis_registry`.

Когда обновляется: из мониторинга закупок под наблюдением (выгруженных в Bitrix24) — жалобы и
договоры не чаще раза в `REGISTRY_INTERVAL`, история заказчика — раз в `HISTORY_INTERVAL`
(каждая — десятки обращений к ЕИС); вручную — `refresh(force=True)`. Сбой реестра оставляет
прежние сведения и пишет `last_error`.

История заказчика: поиск ЕИС по ИНН заказчика даёт все его закупки, но без ОКПД2. Код берётся из
карточки закупки (вкладка лотов), не больше `HISTORY_CARDS`. Итоговая цена и победитель: 44-ФЗ —
вкладка результатов, 223-ФЗ — реестр договоров. Квалификация переиспользуется из разбора либо
извлекается текущим экстрактором документов. `customer_history_for` также обслуживает внешний
запрос Bitrix24 по ИНН/ОКПД2 до публикации ожидаемой закупки (п. 7).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_eis_registry import TenderEisRegistry

REGISTRY_INTERVAL = timedelta(hours=6)
HISTORY_INTERVAL = timedelta(days=7)
HISTORY_CARDS = 8

_METER_RE = re.compile(r"сч[её]тчик|прибор\w*\s+уч[её]т|уч[её]т\w*\s+электро|АСКУЭ|АИИС|ИСУ|интеллектуальн\w+\s+систем", re.I)


def record(db: Session, tender: Tender, *, create: bool = False) -> TenderEisRegistry | None:
    from app.services import tender_twins

    subject = tender_twins.subject(db, tender)
    item = db.get(TenderEisRegistry, subject.id)
    if item is None and create:
        item = TenderEisRegistry(tender_id=subject.id, complaints=[], contracts=[], customer_history=[])
        db.add(item)
    return item


def get(db: Session, tender: Tender) -> dict[str, Any]:
    item = record(db, tender)
    if item is None:
        return {"complaints": [], "contracts": [], "customer_history": [], "registry_checked_at": None}
    return {
        "complaints": item.complaints or [],
        "contracts": item.contracts or [],
        "customer_history": item.customer_history or [],
        "registry_checked_at": item.registry_checked_at,
    }


def _number(tender: Tender) -> str | None:
    digits = re.sub(r"\D", "", tender.registry_number or tender.external_id or "")
    return digits if len(digits) in (11, 19) else None


def _known_contracts(db: Session, tender: Tender) -> list[str]:
    from app.services import eis_card_tables, tender_twins

    numbers = []
    for item in dict.fromkeys((tender, tender_twins.subject(db, tender))):
        card = db.get(TenderCard, item.id)
        for doc in eis_card_tables.card_documents(card.payload if card else None, "contracts"):
            if match := re.search(r"(\d{15,25})", doc.name):
                numbers.append(match.group(1))
    return list(dict.fromkeys(numbers))


def _stems(text: str | None) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{5,}", (text or "").lower().replace("ё", "е"))}


def _okpd_group(code: str | None) -> str | None:
    match = re.match(r"(\d{2}\.\d{2}(?:\.\d{1,2})?)", code or "")
    return match.group(1) if match else None


def _known_qualification(db: Session, number: str) -> list[dict[str, Any]]:
    """Квалификация уже разобранной закупки — без повторного обращения к модели."""

    from app.models.analysis import Requirement, RequirementKind
    from app.services import qualification_service

    tender = db.scalar(
        select(Tender)
        .where((Tender.registry_number == number) | (Tender.external_id == number))
        .order_by(Tender.created_at.desc())
    )
    if tender is None:
        return []
    out = []
    for requirement in db.scalars(
        select(Requirement).where(
            Requirement.tender_id == tender.id,
            Requirement.kind == RequirementKind.PARTICIPANT.value,
        )
    ):
        code, label = qualification_service.classify(requirement.text)
        if not code or "закона" in (label or ""):
            continue
        out.append({"requirement": requirement.text, "qualification_kind": label, **qualification_service.parse(requirement.text)})
    return out


def _document_qualification(number: str, source_url: str) -> tuple[list[dict[str, Any]], str | None]:
    """Квалификация из документов исторической закупки текущим экстрактором текста.

    Полный разбор и запись нового тендера не нужны: читаются до трёх документов, требования
    выделяются тем же справочником `qualification_service`, что и в основном анализе.
    """

    from app.adapters.eis import EisAdapter
    from app.services import document_service, qualification_service
    from app.services.document_extraction import extract_text, sniff_extension

    try:
        refs = EisAdapter().download_documents(number, source_url)
    except Exception as exc:  # noqa: BLE001
        return [], f"документы недоступны: {exc}"
    refs = [ref for ref in refs if "протокол" not in ref.file_name.lower()][:3]
    if not refs:
        return [], "документы для проверки квалификации не опубликованы"
    out = []
    errors = []
    for ref in refs:
        try:
            content = document_service._download_bytes(ref.url)  # noqa: SLF001 - тот же загрузчик ЕИС
            extension = document_service._guess_extension(ref.file_name, ref.url) or sniff_extension(content)  # noqa: SLF001
            text = extract_text(extension, content)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ref.file_name}: {exc}")
            continue
        for line in text.splitlines():
            line = " ".join(line.split()).strip()
            if not 20 <= len(line) <= 1500:
                continue
            code, label = qualification_service.classify(line)
            if not code or "закона" in (label or ""):
                continue
            out.append({"requirement": line, "qualification_kind": label, **qualification_service.parse(line)})
    unique = list({item["requirement"]: item for item in out}.values())
    if not unique:
        errors.append("квалификационные требования из доступного текста не извлечены")
    return unique, "; ".join(errors) if errors else None


def customer_history_for(
    db: Session,
    *,
    customer_inn: str,
    okpd2_code: str | None,
    title: str = "",
    as_of: date | None = None,
    exclude_number: str | None = None,
    fetch_qualification_documents: bool = False,
    completed_only: bool = False,
) -> dict[str, Any]:
    """История по произвольному запросу Bitrix24 или по текущей закупке (п. 7)."""

    from dataclasses import asdict

    from app.adapters import eis_registries
    from app.adapters.eis_card import fetch_card
    from app.adapters.eis_results import EisResultsError, fetch_outcome
    from app.services import eis_card_tables

    group = _okpd_group(okpd2_code)
    title_stems = _stems(title)
    meter = bool(_METER_RE.search(title or ""))

    def score(item: dict[str, Any]) -> int:
        item_title = item.get("title") or ""
        return len(_stems(item_title) & title_stems) + (5 if meter and _METER_RE.search(item_title) else 0)

    candidates = sorted(
        (
            item for item in eis_registries.fetch_customer_purchases(customer_inn, as_of=as_of)
            if item["number"] != exclude_number and item.get("url") and (not title_stems or score(item) >= 2)
        ),
        key=score,
        reverse=True,
    )[:HISTORY_CARDS]
    out = []
    warnings = []
    with eis_registries.client() as http:
        for item in candidates:
            status = (item.get("status") or "").lower()
            if completed_only and re.search(r"подач|при[её]м|рассмотр|определени", status) and "заверш" not in status:
                continue
            try:
                card = fetch_card(http, item["url"])
            except Exception as exc:  # noqa: BLE001 - одна закупка истории не должна ронять остальные
                logger.warning(f"История заказчика: карточка {item['number']} не получена — {exc}")
                warnings.append(f"{item['number']}: карточка недоступна — {exc}")
                continue
            payload = {"sections": [asdict(s) for s in card.sections], "tables": {k: asdict(t) for k, t in card.tables.items()}}
            lots = eis_card_tables.lots(payload)
            codes = [lot.get("okpd2") for lot in lots if lot.get("okpd2")]
            if group and codes and not any(code.startswith(group) for code in codes):
                continue
            item["okpd2"] = "; ".join(dict.fromkeys(c.split(" ")[0] for c in codes)) or None
            item["okpd2_matched"] = bool(group and codes)
            if group and not codes:
                item["source_note"] = "ОКПД2 в карточке не указан — совпадение не подтверждено"
            card_text = " ".join(value for section in payload["sections"] for _, value in section["fields"])
            if (
                exclude_number
                and exclude_number in card_text
                and re.search(r"повторн|взамен|несостояв", card_text, re.IGNORECASE)
            ):
                item["related_to"] = exclude_number
            finals = [d for d in eis_card_tables.card_documents(payload, "protocols") if "итог" in d.name.lower()]
            item["final_protocol"] = finals[-1].name if finals else None
            try:
                if item["law"] == "44":
                    outcome = fetch_outcome(item["number"])
                    item["winner"], item["final_price"] = outcome.winner_name, (float(outcome.contract_value) if outcome.contract_value is not None else None)
                else:
                    known = [m.group(1) for d in eis_card_tables.card_documents(payload, "contracts") if (m := re.search(r"(\d{15,25})", d.name))]
                    if known:
                        contracts = eis_registries.fetch_contracts(item["number"], "223", known[:1])
                        if contracts:
                            item["final_price"] = contracts[0].get("price")
                            item["winner"] = contracts[0].get("supplier")
            except (EisResultsError, eis_registries.EisRegistryError, Exception) as exc:  # noqa: BLE001
                logger.warning(f"История заказчика: итог {item['number']} не получен — {exc}")
                item["outcome_note"] = f"Итоговая цена не получена: {exc}"
            qualification = _known_qualification(db, item["number"])
            qualification_note = None
            if not qualification and fetch_qualification_documents:
                qualification, qualification_note = _document_qualification(item["number"], item["url"])
            item["qualification"] = qualification
            item["qualification_status"] = "EXTRACTED" if qualification else "NOT_EXTRACTED"
            if qualification_note:
                item["qualification_note"] = qualification_note
            out.append(item)
    return {"items": out, "warnings": warnings, "checked_at": datetime.now(timezone.utc)}


def customer_history(db: Session, tender: Tender) -> list[dict[str, Any]]:
    """Закупки заказчика за год с тем же ОКПД2 (до 3-го уровня), итоговой ценой и победителем."""

    from app.services import bitrix_ai_service

    inn = bitrix_ai_service.customer_inn(db, tender)
    if not inn:
        return []
    return customer_history_for(
        db,
        customer_inn=inn,
        okpd2_code=tender.okpd2_code,
        title=tender.title or "",
        exclude_number=_number(tender),
    )["items"]


def refresh(db: Session, tender: Tender, *, force: bool = False, history: bool = True) -> dict[str, Any]:
    """Обновляет сведения реестров. Без `force` — только если прошёл интервал."""

    from app.adapters import eis_registries
    from app.services.bitrix_extra_rows import law_of

    number = _number(tender)
    if number is None:
        return get(db, tender)
    item = record(db, tender, create=True)
    now = datetime.now(timezone.utc)
    errors = []
    if force or item.registry_checked_at is None or now - item.registry_checked_at >= REGISTRY_INTERVAL:
        try:
            item.complaints = eis_registries.fetch_complaints(number)
            item.contracts = eis_registries.fetch_contracts(number, law_of(tender), _known_contracts(db, tender))
            item.registry_checked_at = now
        except Exception as exc:  # noqa: BLE001 - реестр недоступен: прежние сведения остаются
            errors.append(f"реестры жалоб и договоров: {exc}")
    if history and (force or item.history_checked_at is None or now - item.history_checked_at >= HISTORY_INTERVAL):
        try:
            item.customer_history = customer_history(db, tender)
            item.history_checked_at = now
        except Exception as exc:  # noqa: BLE001
            errors.append(f"история заказчика: {exc}")
    item.last_error = "; ".join(errors)[:2000] or None
    db.add(item)
    db.commit()
    return get(db, tender)
