"""Выданные банковские гарантии по закупке и проверка их срока (19.11.2026, раздел 115).

Перечень Bitrix24, п. 32: «Сдвинулся ли срок исполнения из-за дополнительного документа и
требуется ли продление банковской гарантии?». Срок действия выданной гарантии СОВЕ неоткуда
узнать (реестр независимых гарантий по ч. 8.1 ст. 45 44-ФЗ не публикуется) — его вносит
тендерный отдел в карточке закупки. Дальше — кодом: гарантия обеспечения заявки должна
действовать не меньше срока подачи заявок (с запасом, который задаёт документация), гарантия
исполнения — не меньше окончания исполнения договора плюс месяц (типовое требование 44-ФЗ,
ч. 3 ст. 96); не покрывает — «требуется продление».
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_eis_registry import TenderGuarantee
from app.models.user import User

KINDS = {"APPLICATION": "Обеспечение заявки", "CONTRACT": "Обеспечение исполнения договора", "WARRANTY": "Обеспечение гарантийных обязательств"}
# Гарантия исполнения должна действовать дольше договора: 44-ФЗ, ч. 3 ст. 96 — не менее месяца.
CONTRACT_MARGIN = timedelta(days=30)


def _ids(db: Session, tender: Tender) -> list[uuid.UUID]:
    from app.services import tender_twins

    subject = tender_twins.subject(db, tender)
    return list(dict.fromkeys([subject.id, tender.id, *(t.id for t in tender_twins.twins(db, subject))]))


def list_for(db: Session, tender: Tender) -> list[TenderGuarantee]:
    return list(
        db.scalars(select(TenderGuarantee).where(TenderGuarantee.tender_id.in_(_ids(db, tender))).order_by(TenderGuarantee.created_at))
    )


def create(db: Session, tender: Tender, data: dict[str, Any], *, actor: User | None) -> TenderGuarantee:
    from app.services import tender_twins

    if data.get("kind") not in KINDS:
        raise ValueError("Вид гарантии: обеспечение заявки, исполнения договора или гарантийных обязательств")
    item = TenderGuarantee(
        tender_id=tender_twins.subject(db, tender).id,
        kind=data["kind"],
        number=(data.get("number") or None),
        bank=(data.get("bank") or None),
        amount=Decimal(str(data["amount"])) if data.get("amount") not in (None, "") else None,
        valid_until=data["valid_until"],
        note=(data.get("note") or None),
        created_by_id=actor.id if actor else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete(db: Session, tender: Tender, guarantee_id: uuid.UUID) -> None:
    item = db.get(TenderGuarantee, guarantee_id)
    if item is None or item.tender_id not in _ids(db, tender):
        raise LookupError("Гарантия не найдена")
    db.delete(item)
    db.commit()


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def coverage(item: TenderGuarantee, required: Any, *, margin: timedelta = timedelta(0)) -> dict[str, Any]:
    """Покрывает ли гарантия нужную дату. `required` — дата, до которой гарантия должна
    действовать (без запаса); `margin` — запас (для исполнения договора — месяц)."""

    needed = _as_date(required)
    if needed is None:
        return {"covered": None, "needed_until": None, "basis": "Нет даты, с которой сравнить срок гарантии"}
    needed = needed + margin
    covered = item.valid_until >= needed
    return {
        "covered": covered,
        "needed_until": needed,
        "basis": f"Гарантия до {item.valid_until:%d.%m.%Y}, нужна до {needed:%d.%m.%Y}"
        + (" (окончание исполнения + 1 месяц)" if margin else ""),
    }


def required_dates(db: Session, tender: Tender) -> dict[str, Any]:
    """До какой даты должна действовать гарантия каждого вида: заявки — окончание подачи (из
    карточки ЕИС или площадки), исполнения — окончание исполнения договора из реестра ЕИС."""

    from app.services import eis_card_dates, eis_registry_service

    card = eis_card_dates.card_dates(db, tender)
    contracts = eis_registry_service.get(db, tender)["contracts"]
    contract_end = None
    for contract in contracts:
        versions = contract.get("versions") or []
        end = (versions[-1] if versions else contract).get("execution_end") or contract.get("execution_end")
        contract_end = max(filter(None, [contract_end, _as_date(end)]), default=None)
    return {"APPLICATION": card.get("application_end") or tender.application_end, "CONTRACT": contract_end}


def check(db: Session, tender: Tender) -> list[dict[str, Any]]:
    required = required_dates(db, tender)
    out = []
    for item in list_for(db, tender):
        margin = CONTRACT_MARGIN if item.kind == "CONTRACT" else timedelta(0)
        out.append({"guarantee": item, **coverage(item, required.get(item.kind), margin=margin)})
    return out
