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
карточки закупки (вкладка лотов), а карточки читаются только у закупок, похожих по названию на
текущую (общие основы слов или слова про приборы учёта), — не больше `HISTORY_CARDS`. Итоговая
цена и победитель: 44-ФЗ — вкладка результатов, 223-ФЗ — реестр договоров по номеру договора из
карточки.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
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


def customer_history(db: Session, tender: Tender) -> list[dict[str, Any]]:
    """Закупки заказчика за год с тем же ОКПД2 (до 3-го уровня), итоговой ценой и победителем."""

    from dataclasses import asdict

    from app.adapters import eis_registries
    from app.adapters.eis_card import fetch_card
    from app.adapters.eis_results import EisResultsError, fetch_outcome
    from app.services import bitrix_ai_service, eis_card_tables

    inn = bitrix_ai_service.customer_inn(db, tender)
    group = _okpd_group(tender.okpd2_code)
    if not inn:
        return []
    own = _number(tender)
    title_stems = _stems(tender.title)
    # Ближе по названию — первыми: общие слова у заказчика («технологическое присоединение»)
    # есть почти в каждой закупке, поэтому отбирают не они, а код ОКПД2 в карточке ниже.
    meter = bool(_METER_RE.search(tender.title or ""))

    def score(item: dict[str, Any]) -> int:
        title = item.get("title") or ""
        return len(_stems(title) & title_stems) + (5 if meter and _METER_RE.search(title) else 0)

    candidates = sorted(
        (item for item in eis_registries.fetch_customer_purchases(inn)
         if item["number"] != own and item.get("url") and score(item) >= 2),
        key=score,
        reverse=True,
    )[:HISTORY_CARDS]
    out = []
    with eis_registries.client() as http:
        for item in candidates:
            try:
                card = fetch_card(http, item["url"])
            except Exception as exc:  # noqa: BLE001 - одна закупка истории не должна ронять остальные
                logger.warning(f"История заказчика: карточка {item['number']} не получена — {exc}")
                continue
            payload = {"sections": [asdict(s) for s in card.sections], "tables": {k: asdict(t) for k, t in card.tables.items()}}
            lots = eis_card_tables.lots(payload)
            codes = [lot.get("okpd2") for lot in lots if lot.get("okpd2")]
            if group and codes and not any(code.startswith(group) for code in codes):
                continue
            item["okpd2"] = "; ".join(dict.fromkeys(c.split(" ")[0] for c in codes)) or None
            item["okpd2_matched"] = bool(group and codes)
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
            out.append(item)
    return out


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
