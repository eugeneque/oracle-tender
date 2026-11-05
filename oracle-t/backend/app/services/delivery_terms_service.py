"""Модель поставки и исполнение договора: что, куда, кому и когда поставлять (05.11.2026).

Перечень Bitrix24 «Что еще нужно от ИИ», пункты 5, 6, 15, 17, 93 и документация API 1.7.0
(раздел «Модель поставки и исполнение договора»): одна строка `supply_model`, строки
`delivery_point` (грузополучатель + адрес), `delivery_schedule` (этап графика) и одна
`delivery_request_terms` (поставка по заявкам).

Правила Bitrix24, которые здесь соблюдаются кодом, а не только просьбой в промпте:
* связь с позицией, лотом или адресом — только если она прямо указана в документе; иначе
  `relation_status = NOT_ESTABLISHED` (модель могла «додумать» связь — кодом ничего не
  достраиваем);
* ИНН (10 или 12 цифр) и КПП (9) проверяются форматом; не прошедшие — отбрасываются, а не
  исправляются;
* тип договора («рамочный») по числу поставок не выводится — модель отвечает только по тексту;
* неизвестное остаётся пустым, строку без данных не передаём.

Извлечение — отдельный запрос к модели по фрагментам договора, ТЗ и приложений (как позиции
закупки, раздел 74 ARCHITECTURE.md); результат заменяет прежний целиком, сбой модели оставляет
прежний. Хранится в `tender_delivery_terms` (JSONB).
"""

from __future__ import annotations

import re
from typing import Any

import pydantic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_delivery import TenderDeliveryTerms
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.user import User
from app.services.ai_client import fit_chars, run_structured
from app.services.audit import log_action

DESIRED_CHARS = 60_000
MAX_POINTS = 200
MAX_STAGES = 200
_CONTEXT_BEFORE = 2
_CONTEXT_AFTER = 25

# Строки, рядом с которыми лежат условия поставки: адрес и грузополучатель, срок и график,
# заявки заказчика, этапы и партии.
_DELIVERY_RE = re.compile(
    r"поставк|грузополучател|адрес|место\s+(?:доставки|поставки)|график|заявк\w*\s+заказчик"
    r"|заявк\w+\s+на\s+поставк|этап|партия|партии|в\s+течение\s+\d+|календарн\w+\s+дн|рабоч\w+\s+дн"
    r"|рамочн|разов\w+\s+поставк|спецификац",
    re.IGNORECASE,
)

_MODES = {"SINGLE", "MULTIPLE"}
_ORDERS = {"ONE_TIME", "SCHEDULE", "REQUESTS", "SCHEDULE_AND_REQUESTS"}
_LINKS = {"FULL", "PARTIAL", "NONE", "NOT_SPECIFIED"}
_RELATIONS = {"EXPLICIT", "PARTIAL", "NOT_ESTABLISHED"}
_CONTRACT_TYPES = {"FRAMEWORK", "SPECIFIC", "NOT_ESTABLISHED"}
_INN_RE = re.compile(r"^(?:\d{10}|\d{12})$")
_KPP_RE = re.compile(r"^\d{9}$")


class SupplyModelAnswer(pydantic.BaseModel):
    """Все поля обязательны: Yandex AI Studio не принимает схему с необязательными."""

    delivery_mode: str
    delivery_order: str
    general_delivery_terms: str
    nomenclature_link: str
    contract_type: str
    document: str
    quote: str


class PointAnswer(pydantic.BaseModel):
    consignee_name: str
    consignee_inn: str
    consignee_kpp: str
    delivery_address: str
    lot: str
    related_positions: str
    relation_status: str
    document: str
    quote: str


class StageAnswer(pydantic.BaseModel):
    stage_number: str
    delivery_period: str
    trigger_event: str
    quantity_or_volume: str
    consignee_name: str
    delivery_address: str
    lot: str
    related_positions: str
    relation_status: str
    document: str
    quote: str


class RequestsAnswer(pydantic.BaseModel):
    """`present` — предусмотрены ли заявки заказчика; тройные значения — `yes`/`no`/`unknown`."""

    present: bool
    request_method: str
    fulfillment_term: str
    min_volume: str
    max_volume: str
    request_period: str
    nomenclature_defined_in_request: str
    quantity_defined_in_request: str
    address_defined_in_request: str
    consignee_defined_in_request: str
    document: str
    quote: str


class DeliveryAnswer(pydantic.BaseModel):
    supply_model: SupplyModelAnswer
    points: list[PointAnswer]
    stages: list[StageAnswer]
    requests: RequestsAnswer


_SYSTEM_PROMPT = """Ты разбираешь закупочную документацию поставщика приборов учёта \
электроэнергии. Извлеки условия ПОСТАВКИ из проекта договора, ТЗ и приложений.

Общие правила:
- Только то, что прямо сказано в тексте. Не придумывай и не достраивай. Неизвестное — пустая строка.
- Не связывай товары, адреса, грузополучателей и этапы, если связь не указана в документах: \
в таком случае relation_status = NOT_ESTABLISHED, а related_positions пусто.
- document — имя файла из строки «=== имя файла ===» над текстом, quote — короткая цитата (до \
200 символов).

supply_model (одна запись):
- delivery_mode: SINGLE — одна поставка; MULTIPLE — несколько поставок (партий, этапов, по \
заявкам); пусто — не определить.
- delivery_order: ONE_TIME — разовая; SCHEDULE — по графику; REQUESTS — по заявкам заказчика; \
SCHEDULE_AND_REQUESTS — и по графику, и по заявкам; пусто — не определить.
- general_delivery_terms: общий срок поставки как в документе («В течение 60 дней с даты \
договора»).
- nomenclature_link: связана ли номенклатура с адресами и грузополучателями — FULL (каждая \
позиция привязана), PARTIAL (часть), NONE (адреса есть, привязки нет), NOT_SPECIFIED (адресов \
нет).
- contract_type: FRAMEWORK — договор прямо назван рамочным или предусматривает поставку по \
мере заявок без твёрдого объёма; SPECIFIC — поставка конкретного объёма; NOT_ESTABLISHED — \
не определить. Рамочным по одному лишь числу поставок договор НЕ считается.

points — каждая пара «грузополучатель + адрес» отдельной записью (у грузополучателя пять \
адресов — пять записей). consignee_inn — только цифры (10 или 12), consignee_kpp — 9 цифр; не \
угадывай ИНН по названию. lot — номер лота, если закупка делится на лоты, иначе "1". \
related_positions — например «Позиции 1–3», только если связь указана. relation_status: \
EXPLICIT — связь с позициями прямо указана; PARTIAL — частично; NOT_ESTABLISHED — не указана.

stages — каждый этап, партия или период графика поставки отдельной записью: stage_number, \
delivery_period («До 15.03.2027», «в течение 30 дней»), trigger_event — от какого события \
считается срок («с даты заключения договора»), quantity_or_volume («100 шт.»), грузополучатель \
и адрес — если указаны для этого этапа. Графика нет — пустой список; график не составляй.

requests — поставка по заявкам заказчика. present = true, только если договор предусматривает \
заявки. request_method — как подаётся заявка; fulfillment_term — срок исполнения заявки; \
min_volume/max_volume — как в документе или пусто; request_period — период подачи заявок. \
*_defined_in_request — определяется ли в заявке номенклатура / количество / адрес / \
грузополучатель: yes, no или unknown (в документе не сказано). Если заявок нет — present = false \
и пустые строки, "unknown".
Отвечай по-русски."""


def _ordered_documents(db: Session, tender: Tender) -> list[TenderDocument]:
    documents = list(
        db.scalars(
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id, TenderDocument.extracted_text.is_not(None))
            .order_by(TenderDocument.created_at)
        )
    )
    # Условия поставки — в проекте договора и ТЗ; извещение и протоколы идут в конце и
    # обрезаются первыми.
    rank = {DocumentClass.CONTRACT.value: 0, DocumentClass.TZ_DESCRIPTION.value: 1, DocumentClass.SSR.value: 2}
    documents.sort(key=lambda d: (not d.is_priority_source, rank.get(d.document_class or "", 9)))
    return documents


def delivery_text(db: Session, tender: Tender, limit: int) -> str:
    """Фрагменты документов вокруг слов о поставке: документ целиком в окно не помещается."""

    parts: list[str] = []
    length = 0
    for document in _ordered_documents(db, tender):
        lines = document.extracted_text.splitlines()
        keep: set[int] = set()
        for index, line in enumerate(lines):
            if _DELIVERY_RE.search(line):
                keep.update(range(max(0, index - _CONTEXT_BEFORE), min(len(lines), index + _CONTEXT_AFTER)))
        if not keep:
            continue
        chunks: list[str] = []
        previous = None
        for index in sorted(keep):
            if previous is not None and index != previous + 1:
                chunks.append("…")
            chunks.append(lines[index])
            previous = index
        body = "\n".join(chunks)
        header = f"\n=== {document.file_name} ===\n"
        room = limit - length - len(header)
        if room <= 500:
            break
        parts.append(header + body[:room])
        length += len(header) + min(len(body), room)
    return "".join(parts)


# --- нормализация ответа ----------------------------------------------------------------------


def _clean(value: str | None, limit: int = 500) -> str | None:
    value = " ".join((value or "").split()).strip(" ;")
    return value[:limit] or None


def _enum(value: str | None, allowed: set[str]) -> str | None:
    value = (value or "").strip().upper()
    return value if value in allowed else None


def _tri(value: str | None) -> bool | None:
    return {"yes": True, "no": False}.get((value or "").strip().lower())


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, "")}


def _relation(value: str | None, related: str | None) -> str:
    """Связь «EXPLICIT» без указанных позиций — выдумка модели: считаем её не установленной."""

    relation = _enum(value, _RELATIONS) or "NOT_ESTABLISHED"
    if relation != "NOT_ESTABLISHED" and not related:
        return "NOT_ESTABLISHED"
    return relation


def normalize(answer: DeliveryAnswer) -> dict[str, Any]:
    model = answer.supply_model
    supply_model = _compact(
        {
            "delivery_mode": _enum(model.delivery_mode, _MODES),
            "delivery_order": _enum(model.delivery_order, _ORDERS),
            "general_delivery_terms": _clean(model.general_delivery_terms, 500),
            "nomenclature_link": _enum(model.nomenclature_link, _LINKS),
            "contract_type": _enum(model.contract_type, _CONTRACT_TYPES),
            "document": _clean(model.document),
            "quote": _clean(model.quote, 600),
        }
    )
    # Строка модели поставки нужна, только если что-то определено помимо источника.
    if not any(k in supply_model for k in ("delivery_mode", "delivery_order", "general_delivery_terms")):
        supply_model = {}

    points: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in answer.points[:MAX_POINTS]:
        inn, kpp = _digits(item.consignee_inn), _digits(item.consignee_kpp)
        name, address = _clean(item.consignee_name, 500), _clean(item.delivery_address, 1000)
        if not name and not address:
            continue
        marker = (_digits(item.consignee_inn), (name or "").casefold(), (address or "").casefold())
        if marker in seen:
            continue
        seen.add(marker)
        related = _clean(item.related_positions, 200)
        points.append(
            _compact(
                {
                    "consignee_name": name,
                    "consignee_inn": inn if _INN_RE.match(inn) else None,
                    "consignee_kpp": kpp if _KPP_RE.match(kpp) else None,
                    "delivery_address": address,
                    "lot_number": _clean(item.lot, 30) or "1",
                    "related_positions": related,
                    "relation_status": _relation(item.relation_status, related),
                    "document": _clean(item.document),
                    "quote": _clean(item.quote, 600),
                }
            )
        )

    stages: list[dict[str, Any]] = []
    for item in answer.stages[:MAX_STAGES]:
        period, volume = _clean(item.delivery_period, 300), _clean(item.quantity_or_volume, 200)
        if not period and not volume:
            continue
        related = _clean(item.related_positions, 200)
        stages.append(
            _compact(
                {
                    "stage_number": _clean(item.stage_number, 30) or str(len(stages) + 1),
                    "delivery_period": period,
                    "trigger_event": _clean(item.trigger_event, 300),
                    "quantity_or_volume": volume,
                    "consignee_name": _clean(item.consignee_name, 500),
                    "delivery_address": _clean(item.delivery_address, 1000),
                    "lot_number": _clean(item.lot, 30) or "1",
                    "related_positions": related,
                    "relation_status": _relation(item.relation_status, related),
                    "document": _clean(item.document),
                    "quote": _clean(item.quote, 600),
                }
            )
        )

    req = answer.requests
    requests: dict[str, Any] = {}
    if req.present:
        requests = _compact(
            {
                "request_method": _clean(req.request_method, 300),
                "fulfillment_term": _clean(req.fulfillment_term, 300),
                "min_volume": _clean(req.min_volume, 200) or "Не указан",
                "max_volume": _clean(req.max_volume, 200) or "Не указан",
                "request_period": _clean(req.request_period, 300),
                "nomenclature_defined_in_request": _tri(req.nomenclature_defined_in_request),
                "quantity_defined_in_request": _tri(req.quantity_defined_in_request),
                "address_defined_in_request": _tri(req.address_defined_in_request),
                "consignee_defined_in_request": _tri(req.consignee_defined_in_request),
                "document": _clean(req.document),
                "quote": _clean(req.quote, 600),
            }
        )
    return {"supply_model": supply_model, "points": points, "stages": stages, "requests": requests}


# --- извлечение и чтение ----------------------------------------------------------------------


def extract(db: Session, tender: Tender, *, actor: User | None = None) -> dict[str, int]:
    """Извлекает условия поставки и заменяет прежние. Без текста документации модель не
    вызывается, прежнее не трогается. Сбой модели поднимается — вызывающий решает, что делать."""

    reserved = len(_SYSTEM_PROMPT) + 500
    text = delivery_text(db, tender, fit_chars(db, DESIRED_CHARS, reserved=reserved))
    if not text:
        return {"points": 0, "stages": 0}
    answer = run_structured(
        db,
        system_prompt=_SYSTEM_PROMPT,
        user_text=f"ЗАКУПКА: {tender.title}\n\nДОКУМЕНТАЦИЯ (фрагменты об условиях поставки):\n{text}",
        response_model=DeliveryAnswer,
        temperature=0.0,
    )
    payload = normalize(answer)
    record = db.get(TenderDeliveryTerms, tender.id) or TenderDeliveryTerms(tender_id=tender.id)
    record.payload = payload
    db.add(record)
    log_action(
        db,
        component="delivery_terms",
        action=f"extract_delivery_terms:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"Мест поставки {len(payload['points'])}, этапов графика {len(payload['stages'])}, "
            f"модель поставки {'есть' if payload['supply_model'] else 'не определена'}, "
            f"заявки {'есть' if payload['requests'] else 'нет'}"
        ),
        user_id=actor.id if actor else None,
    )
    db.commit()
    return {"points": len(payload["points"]), "stages": len(payload["stages"])}


def get_terms(db: Session, tender: Tender) -> dict[str, Any]:
    record = db.get(TenderDeliveryTerms, tender.id)
    empty = {"supply_model": {}, "points": [], "stages": [], "requests": {}}
    return {**empty, **(record.payload if record else {})}
