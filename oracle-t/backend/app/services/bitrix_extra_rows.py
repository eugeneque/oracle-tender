"""Дополнительные строки вкладки «ИИ анализ и решения» Bitrix24 (17.11.2026, раздел 113).

Перечень «Дополнение п.95. Что еще нужно от ИИ»: лоты (п. 1), история заказчика (п. 7), запросы
разъяснений (пп. 33–35), заточенность ТЗ и обоснование жалобы (пп. 36–40), раунды цен (п. 78),
выводы по итогам (пп. 75, 80–83, 88, 89, 91, 92), сведения ФАС из журнала ЕИС (пп. 41–47).

Все строки — типов из рекомендованного списка API 1.7.0 (`checklist`, `clarification`,
`price_round`, `fas_signal`, `fas_appeal`, `fas_decision`, `obligation`, `procedure_result`):
новых полей CRM и кодов событий не вводят. Всё — кодом по уже извлечённым данным (требования,
матрица, протоколы, условия договора, карточка ЕИС, журнал событий), без новых запросов к
модели: «используй уже извлечённые данные без повторных независимых записей».

Правила, которые соблюдаются здесь:
* выводы, а не решения: подача жалобы, запрос разъяснений, подписание договора — решения
  сотрудника, строки только готовят основания и сроки (статус «требуется проверка»);
* расчётные даты (срок подписания, срок жалобы, срок поставки) — с основанием расчёта; нет
  исходной даты или числа дней — даты нет, а не «примерно»;
* сроки обжалования — по общему правилу закона (44-ФЗ ст. 105, 135-ФЗ ст. 18.1 для 223-ФЗ), с
  пометкой проверить: особые случаи (закрытые процедуры, ГОЗ) код не различает.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import ComplianceStatus, Requirement, RequirementKind, WinPercentage
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender, TenderStage
from app.models.tender_card import TenderCard
from app.models.tender_event import TenderEvent
from app.services import document_diff, eis_card_tables, qualification_service

if TYPE_CHECKING:
    from app.services.ai_conclusion_service import BrandLock

Sections = dict[str, list[dict[str, Any]]]

MAX_CLARIFICATIONS = 15
MAX_HISTORY = 15


def _b():  # отложенный импорт: bitrix_ai_service импортирует этот модуль
    from app.services import bitrix_ai_service

    return bitrix_ai_service


def _card(db: Session, tender: Tender) -> dict | None:
    from app.services import tender_twins

    for item in dict.fromkeys((tender, tender_twins.subject(db, tender))):
        card = db.get(TenderCard, item.id)
        if card is not None and card.payload:
            return card.payload
    return None


def law_of(tender: Tender) -> str | None:
    """44-ФЗ — реестровый номер из 19 цифр, 223-ФЗ — из 11; иначе закон по номеру не понять."""

    digits = re.sub(r"\D", "", tender.registry_number or tender.external_id or "")
    if len(digits) == 19:
        return "44"
    if len(digits) == 11:
        return "223"
    return None


def _fmt(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M") + (f" ({value.tzname()})" if value.tzinfo else "")
    return value.strftime("%d.%m.%Y")


def _add_days(start: date, days: int, working: bool) -> date:
    if not working:
        return start + timedelta(days=days)
    current, left = start, days
    while left > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current


_DAYS_RE = re.compile(r"(\d{1,3})\s*(?:\(\w+\)\s*)?(рабоч|календарн|дн|день)", re.I)
_NOT_LATER_RE = re.compile(r"не\s+позднее\s+(\d{1,3})", re.I)


def term_days(term: str | None) -> int | None:
    """Число дней срока: «не позднее N» главнее, иначе последнее «N дней» (у «не ранее 10 и не
    позднее 20 дней» предельный — 20)."""

    text = term or ""
    if match := _NOT_LATER_RE.search(text):
        return int(match.group(1))
    found = _DAYS_RE.findall(text)
    return int(found[-1][0]) if found else None


# --- п. 1: лоты ---------------------------------------------------------------------------------


def lot_rows(db: Session, tender: Tender, sections: Sections) -> None:
    b = _b()
    lots = eis_card_tables.lots(_card(db, tender))
    if not lots:
        return
    for lot in lots:
        sections["preparation"].append(
            b._row(
                f"lot-{lot['lot_number']}",
                "checklist",
                f"Лот {lot['lot_number']} из {len(lots)}",
                {
                    "lot_number": lot["lot_number"],
                    "lot_name": b._clip(lot["name"], 1000),
                    "lots_total": len(lots),
                    "lot_price": lot.get("price"),
                    "okpd2": lot.get("okpd2"),
                },
                evidence=b._evidence("Карточка закупки ЕИС, «Список лотов»", lot.get("price_text") or lot["name"]),
            )
        )


# --- п. 7: история заказчика ---------------------------------------------------------------------


def _okpd_group(code: str | None) -> str | None:
    match = re.match(r"(\d{2}\.\d{2}(?:\.\d{1,2})?)", code or "")
    return match.group(1) if match else None


def customer_history_rows(db: Session, tender: Tender, sections: Sections) -> None:
    """Закупки этого заказчика по тому же ОКПД2 за год — из того, что собрала СОВА (не полный
    реестр ЕИС): дата, НМЦК, итоговая цена и победитель из протоколов, квалификация."""

    from app.services.protocol_results_service import get_results

    b = _b()
    group = _okpd_group(tender.okpd2_code)
    if not tender.customer_name or not group:
        return
    since = (tender.publish_date or date.today()) - timedelta(days=365)
    others = db.scalars(
        select(Tender)
        .where(
            Tender.id != tender.id,
            Tender.customer_name == tender.customer_name,
            Tender.okpd2_code.like(f"{group}%"),
            Tender.publish_date >= since,
            Tender.analysis_tender_id.is_(None),
        )
        .order_by(Tender.publish_date.desc())
        .limit(MAX_HISTORY)
    ).all()
    for other in others:
        lots = get_results(db, other)["lots"]
        final = next((lot for lot in lots if lot.get("final")), None)
        qualification = sorted(
            {
                label
                for requirement in db.scalars(
                    select(Requirement.text).where(
                        Requirement.tender_id == other.id, Requirement.kind == RequirementKind.PARTICIPANT.value
                    )
                )
                for _, label in [qualification_service.classify(requirement)]
                if label and "закона" not in label
            }
        )
        sections["preparation"].append(
            b._row(
                b._key("customer-history", other.id),
                "checklist",
                f"История заказчика: {b._clip(other.title, 200)}",
                {
                    "procedure_number": b.procedure_number(other),
                    "publish_date": _fmt(other.publish_date),
                    "okpd2": other.okpd2_code,
                    "initial_price": b._number(other.price),
                    "final_price": (final or {}).get("winner_price"),
                    "winner": (final or {}).get("winner"),
                    "winner_inn": (final or {}).get("winner_inn"),
                    "qualification": "; ".join(qualification) or None,
                    "url": other.source_url,
                    "note": "По закупкам, собранным СОВОЙ; не полный реестр ЕИС",
                },
            )
        )


# --- пп. 33–35: запросы разъяснений -------------------------------------------------------------

_VAGUE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"по\s+согласовани\w*\s+с\s+заказчик", re.I), "Какие условия согласования и какое значение будет принято?"),
    (re.compile(r"или\s+(?:аналог|эквивалент)(?![^.;]{0,80}(?:характеристик|параметр|не\s+хуже))", re.I),
     "По каким параметрам определяется эквивалентность?"),
    (re.compile(r"\bи\s+т\.\s?[дп]\.|\bи\s+др\.|\bи\s+прочее", re.I), "Какой полный перечень имеется в виду?"),
    (re.compile(r"не\s+хуже\s+(?!\d)|высок\w+\s+качеств|современн\w+|надлежащ\w+\s+качеств|достаточн\w+", re.I),
     "Какое конкретное значение параметра требуется?"),
    (re.compile(r"при\s+необходимост|по\s+требовани\w+\s+заказчик|по\s+усмотрени", re.I),
     "В каких случаях требование применяется и в каком объёме?"),
    (re.compile(r"ориентировочн|примерн\w+\s+(?:количеств|объ[её]м)", re.I), "Какой объём является обязательным?"),
)


def clarification_rows(
    db: Session,
    tender: Tender,
    requirements: list[Requirement],
    documents: dict,
    lock: "BrandLock | None",
    sections: Sections,
) -> None:
    """Неоднозначные требования (п. 33), проект запроса (п. 34) и на что влияет ответ (п. 35).
    Запрос — проект: отправляет ли его тендерный отдел, решает сотрудник."""

    b = _b()
    count = 0
    if lock is not None and not lock.is_ours and lock.equivalent_allowed is not True:
        name = lock.manufacturer.brand_name or lock.manufacturer.legal_name
        quote = lock.clause or ("Модели: " + ", ".join(lock.models) if lock.models else name)
        sections["preparation"].append(
            b._row(
                f"clarification-brand-{lock.manufacturer.id}",
                "clarification",
                f"Эквивалент товара {name}",
                {
                    "requirement": b._clip(quote),
                    "location": "ТЗ, требования к товару",
                    "question": "Допускается ли поставка эквивалента указанных моделей и по каким параметрам "
                                "определяется эквивалентность?",
                    "request_draft": _draft(None, quote, "Просим сообщить, допускается ли поставка эквивалента "
                                            "указанных моделей, и перечислить параметры эквивалентности."),
                    "affects": "Участие; ТЗ и характеристики",
                },
                evidence=b._evidence(None, lock.clause),
                status=b.STATUS_REVIEW,
            )
        )
        count += 1
    for requirement in requirements:
        if count >= MAX_CLARIFICATIONS:
            break
        text = requirement.text or ""
        hit = next(((p, q) for p, q in _VAGUE if p.search(text)), None)
        if hit is None:
            continue
        document = documents.get(requirement.source_document_id) if requirement.source_document_id else None
        areas = document_diff.impact_areas(text)
        base = "Участие и состав заявки" if requirement.kind == RequirementKind.PARTICIPANT.value else "ТЗ и характеристики"
        if base not in areas:
            areas.insert(0, base)
        sections["preparation"].append(
            b._row(
                b._key("clarification", requirement.kind, requirement.normalized_text or text),
                "clarification",
                f"Уточнить: {b._requirement_title(requirement)}",
                {
                    "requirement": b._clip(text),
                    "location": document or "Документация закупки",
                    "position": requirement.position,
                    "ambiguity": hit[0].search(text).group(0),
                    "question": hit[1],
                    "request_draft": _draft(document, text, hit[1]),
                    "affects": "; ".join(areas),
                    "criticality": b._CRITICALITY.get(requirement.criticality),
                },
                evidence=b._evidence(document, text),
                status=b.STATUS_REVIEW,
            )
        )
        count += 1


def _draft(document: str | None, quote: str, question: str) -> str:
    where = f"В документе «{document}»" if document else "В документации закупки"
    quote = " ".join(quote.split())
    if len(quote) > 400:
        quote = quote[:399] + "…"
    return (
        f"Просим разъяснить положения документации закупки. {where} указано: «{quote}». "
        f"{question} Просим указать конкретные значения (диапазоны) либо порядок их определения."
    )


# --- п. 36–40: заточенность ТЗ и обоснование жалобы на документацию ------------------------------


def _meeting_manufacturers(db: Session, tender: Tender) -> tuple[list[str], int]:
    manufacturers = {m.id: m for m in db.scalars(select(Manufacturer))}
    wins = db.scalars(
        select(WinPercentage).where(WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True))
    ).all()
    meeting = [
        manufacturers[w.manufacturer_id].brand_name or manufacturers[w.manufacturer_id].legal_name
        for w in wins
        if w.manufacturer_id in manufacturers and (w.verdict or "") in ("passes", "caveats")
    ]
    return meeting, len(wins)


def brand_lock_rows(db: Session, tender: Tender, lock: "BrandLock | None", sections: Sections) -> None:
    """Сигнал ФАС с уверенностью и сравнением (п. 36) и проект обоснования жалобы на положения
    ТЗ (пп. 37–40): что оспаривается, доводы, подтверждающие документы, предельный срок."""

    b = _b()
    if lock is None or lock.is_ours:
        return
    if lock.equivalent_allowed is not False and not lock.exclusive:
        return
    name = lock.manufacturer.brand_name or lock.manufacturer.legal_name
    meeting, compared = _meeting_manufacturers(db, tender)
    reason = (
        "эквивалент прямо запрещён" if lock.equivalent_allowed is False else
        "требуется фирменное ПО или сервис производителя: " + ", ".join(lock.exclusive)
    )
    signs = [f"ТЗ называет модели одного производителя ({name})", reason]
    if lock.critical_mentions:
        signs.append(f"модели названы в критичных требованиях: {lock.critical_mentions}")
    if compared:
        signs.append(
            f"по матрице соответствия требованиям отвечают {len(meeting)} из {compared} производителей справочника"
            + (f": {', '.join(meeting)}" if meeting else "")
        )
    confidence = 0.85 if lock.equivalent_allowed is False else 0.65
    if compared and len(meeting) <= 1:
        confidence = min(0.95, confidence + 0.1)
    sections["fas"].append(
        b._row(
            f"brand-lock-{lock.manufacturer.id}",
            "fas_signal",
            f"ТЗ под товарный знак {name}",
            {
                "requirement": "Модели: " + ", ".join(lock.models) if lock.models else None,
                "reason": f"ТЗ называет модели одного производителя, {reason}",
                "signs": "; ".join(signs),
                "comparison": f"Соответствуют: {len(meeting)} из {compared}" if compared else None,
                "risk": "Возможное ограничение конкуренции",
                "question": "Допускается ли эквивалент указанной модели?",
            },
            evidence=b._evidence(None, lock.clause),
            confidence=confidence,
            status=b.STATUS_REVIEW,
        )
    )
    law = law_of(tender)
    deadline = tender.application_end
    sections["fas"].append(
        b._row(
            f"fas-appeal-documentation-{lock.manufacturer.id}",
            "fas_appeal",
            "Проект обоснования жалобы на положения ТЗ",
            {
                "contested": "Положения ТЗ (извещения) о товаре: " + ("; ".join(lock.models) if lock.models else name),
                "justification": (
                    "Описание объекта закупки указывает товарный знак одного производителя без возможности "
                    "поставки эквивалента (" + reason + "), что ограничивает число участников"
                    + (" (44-ФЗ, ст. 33)" if law == "44" else " (223-ФЗ, ч. 6.1 ст. 3)" if law == "223" else "")
                    + ". " + (_capital("; ".join(signs[2:])) + "." if len(signs) > 2 else "")
                ).strip(),
                "supporting_documents": "ТЗ с цитатой о товарном знаке; матрица соответствия СОВЫ по производителям; "
                                        "карточки моделей-аналогов из реестра ФГИС",
                "deadline": _fmt(deadline),
                "deadline_basis": "Жалоба на положения документации — до окончания срока подачи заявок "
                                  "(общее правило; проверить юристу)",
                "decision": "Подавать ли жалобу — решает тендерный отдел",
            },
            evidence=b._evidence(None, lock.clause),
            confidence=confidence,
            status=b.STATUS_REVIEW,
        )
    )


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# --- п. 41–47: жалобы и решения из журнала ЕИС ----------------------------------------------------

_FAS_DECISION_LABELS = {"COMPLAINT": "Жалоба подана", "DECISION": "Решение по жалобе", "PRESCRIPTION": "Предписание"}


def fas_event_rows(db: Session, tender: Tender, sections: Sections) -> None:
    from app.services import tender_twins

    b = _b()
    ids = [tender.id, *(t.id for t in tender_twins.twins(db, tender))]
    events = db.scalars(
        select(TenderEvent)
        .where(TenderEvent.tender_id.in_(ids), TenderEvent.field_key.like("fas:%"))
        .order_by(TenderEvent.detected_at)
    ).all()
    for event in events:
        kind = (event.data or {}).get("fas_event") or "COMPLAINT"
        text = event.new_value or ""
        lowered = text.lower()
        outcome = (
            "Жалоба признана обоснованной" if re.search(r"(?<!не)обоснованн", lowered) and "необоснованн" not in lowered
            else "Жалоба признана необоснованной" if "необоснованн" in lowered
            else None
        )
        sections["fas"].append(
            b._row(
                f"fas-{event.event_id}",
                "fas_decision" if kind != "COMPLAINT" else "fas_appeal",
                _FAS_DECISION_LABELS.get(kind, "Жалоба") + " (журнал ЕИС)",
                {
                    "event": text[:1500],
                    "date": _fmt(event.effective_at) if event.effective_at else None,
                    "outcome": outcome,
                    "prescription": "Да" if kind == "PRESCRIPTION" else None,
                    "note": "Из журнала событий ЕИС; текст жалобы, решения и предписания — в реестре жалоб ЕИС",
                },
                evidence=b._evidence("Журнал событий закупки ЕИС", text),
                status=b.STATUS_REVIEW,
            )
        )


# --- пп. 75, 80–83, 88, 89, 91, 92: выводы по итогам ---------------------------------------------

_CLAUSE_RE = re.compile(r"\bп(?:ункт|\.)\s*\d|\bраздел\w*\s+\d|\bприложени\w+\s*№?\s*\d|\d+\.\d+", re.I)


def _final_protocol_at(payload: dict | None, lot: dict[str, Any]) -> datetime | date | None:
    """Момент размещения итогового протокола: из карточки ЕИС (с поясом), иначе дата из текста."""

    docs = [d for d in eis_card_tables.card_documents(payload, "protocols") if d.active and d.placed_at]
    finals = [d for d in docs if "итог" in d.name.lower()]
    if finals:
        return max(d.placed_at for d in finals)
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", lot.get("protocol_date") or "")
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None
    return None


def _words(text: str | None) -> set[str]:
    """Основы слов (первые 5 букв): «протокол»/«протокола», «счётчик»/«счётчиком» совпадают."""

    return {w[:5] for w in re.findall(r"[а-яёa-z0-9]{5,}", (text or "").lower().replace("ё", "е"))}


def outcome_rows(
    db: Session,
    tender: Tender,
    requirements: list[Requirement],
    cells: dict,
    sections: Sections,
) -> None:
    from app.services.contract_conditions_service import get_conditions
    from app.services.protocol_results_service import get_results

    b = _b()
    results = get_results(db, tender)
    payload = _card(db, tender)
    conditions = get_conditions(db, tender)["conditions"]
    law = law_of(tender)

    # Раунды цен (п. 78).
    for item in results.get("rounds") or []:
        label = item.get("name") or (f"Заявка №{item['application_number']}" if item.get("application_number") else "Участник")
        sections["participants_prices"].append(
            b._row(
                b._key("price-round", item.get("lot_number"), item.get("round"), item.get("inn") or label, item.get("price")),
                "price_round",
                f"Раунд {item.get('round') or '—'}: {label}",
                {
                    "lot_number": item.get("lot_number"),
                    "round": item.get("round"),
                    "participant": item.get("name"),
                    "inn": item.get("inn"),
                    "application_number": item.get("application_number"),
                    "price": item.get("price"),
                    "bid_time": item.get("bid_time"),
                    "is_ours": item.get("is_ours") or None,
                },
                evidence=b._evidence(item.get("document"), item.get("quote")),
            )
        )

    participants = results.get("participants") or []
    for lot in results.get("lots") or []:
        number = lot.get("lot_number") or "1"
        suffix = "" if number == "1" else f"-lot-{number}"
        title_suffix = "" if number == "1" else f" (лот {number})"
        members = [p for p in participants if (p.get("lot_number") or "1") == number]
        ours = next((p for p in members if p.get("is_ours")), None)
        winner = next((p for p in members if p.get("inn") and p.get("inn") == lot.get("winner_inn")), None)
        our_result = lot.get("our_result")
        evidence = b._evidence(lot.get("protocol_document"), lot.get("quote"))
        protocol_at = _final_protocol_at(payload, lot)

        # п. 75: подавали заявку, итоговый протокол есть, а нас в нём нет.
        if ours is None and lot.get("final") and tender.stage in (
            TenderStage.APPLICATION_SUBMITTED.value, TenderStage.WON.value, TenderStage.LOST.value
        ):
            anonymous = members and all(not p.get("inn") and not p.get("name") for p in members)
            sections["participants_prices"].append(
                b._row(
                    f"our-application-missing{suffix}",
                    "procedure_result",
                    "Нашей заявки нет в итоговом протоколе" + title_suffix,
                    {
                        "lot_number": number,
                        "result": "Требуется проверка: заявка подана, но в протоколе не найдена по ИНН",
                        "reason": "Участники в протоколе обезличены (только номера заявок) — сверить по номеру заявки"
                                  if anonymous else "Проверить, подана ли заявка на этот лот и под каким ИНН",
                    },
                    evidence=evidence,
                    status=b.STATUS_REVIEW,
                )
            )

        # п. 88: победа подтверждена итоговым протоколом.
        if our_result == "WIN":
            confirmed = bool(lot.get("final")) and not lot.get("conflicts")
            sections["participants_prices"].append(
                b._row(
                    f"win-confirmation{suffix}",
                    "procedure_result",
                    "Победа подтверждена итоговым протоколом" + title_suffix if confirmed
                    else "Победа не подтверждена итоговым протоколом" + title_suffix,
                    {
                        "lot_number": number,
                        "confirmed": "Да" if confirmed else "Нет",
                        "protocol_kind": lot.get("protocol_kind"),
                        "protocol_date": _fmt(protocol_at) or lot.get("protocol_date"),
                        "protocol_number": lot.get("protocol_number"),
                        "final_price": lot.get("winner_price"),
                    },
                    evidence=evidence,
                    confidence=(lot.get("confidence") or {}).get("result"),
                    status=b.STATUS_EXTRACTED if confirmed else b.STATUS_REVIEW,
                )
            )
            _after_win_rows(db, tender, number, suffix, title_suffix, protocol_at, payload, conditions, sections)

        # пп. 80–82: почему не прошли — срок, ТЗ, квалификация.
        if ours is not None:
            category = ours.get("rejection_category")
            reason = ours.get("rejection_reason")
            if category == "DEADLINE" or (our_result == "LOSS" and ours.get("delivery_term") and (winner or {}).get("delivery_term")):
                sections["participants_prices"].append(
                    b._row(
                        f"deadline-comparison{suffix}",
                        "procedure_result",
                        "Срок поставки: наш и победителя" + title_suffix,
                        {
                            "lot_number": number,
                            "our_delivery_term": ours.get("delivery_term"),
                            "winner_delivery_term": (winner or {}).get("delivery_term"),
                            "rejection_reason": b._clip(reason) if category == "DEADLINE" else None,
                            "note": None if (winner or {}).get("delivery_term") else "Срок победителя в протоколе не опубликован",
                        },
                        evidence=evidence,
                        status=b.STATUS_REVIEW,
                    )
                )
            if category == "TECHNICAL" and reason:
                reason_words = _words(reason)
                failing = []
                for requirement in requirements:
                    cell = cells.get(requirement.id)
                    entry = cell[0] if cell else None
                    overlap = len(reason_words & _words(requirement.text))
                    if overlap >= 2 or (entry and entry.status == ComplianceStatus.NOT_MEETS.value and overlap >= 1):
                        failing.append(
                            f"{b._requirement_title(requirement)} — "
                            + (b._COMPLIANCE.get(entry.status, "нет вердикта") if entry else "нет вердикта")
                        )
                sections["participants_prices"].append(
                    b._row(
                        f"technical-rejection{suffix}",
                        "procedure_result",
                        "Отклонение по ТЗ: требования" + title_suffix,
                        {
                            "lot_number": number,
                            "rejection_reason": b._clip(reason),
                            "requirements": "\n".join(failing[:15]) or "Требования из причины отклонения не сопоставлены с ТЗ",
                            "note": "Сопоставление по словам причины отклонения и матрице соответствия МИРТЕК",
                        },
                        evidence=evidence,
                        status=b.STATUS_REVIEW,
                    )
                )
            if category in ("QUALIFICATION", "DOCUMENTS") and reason:
                company = qualification_service.company_facts(db)
                reason_words = _words(reason)
                criteria = []
                for requirement in requirements:
                    if requirement.kind != RequirementKind.PARTICIPANT.value:
                        continue
                    if len(reason_words & _words(requirement.text)) < 2:
                        continue
                    assessed = qualification_service.assess(requirement.text, company)
                    criteria.append(f"{b._requirement_title(requirement)} — {assessed.status_label}")
                sections["participants_prices"].append(
                    b._row(
                        f"qualification-rejection{suffix}",
                        "procedure_result",
                        "Отклонение по квалификации: критерии" + title_suffix,
                        {
                            "lot_number": number,
                            "rejection_reason": b._clip(reason),
                            "criteria": "\n".join(criteria[:15]) or "Критерии из причины отклонения не сопоставлены с требованиями",
                        },
                        evidence=evidence,
                        status=b.STATUS_REVIEW,
                    )
                )

            # п. 83 и 38–40: основания проверить обжалование итогов.
            grounds = _appeal_grounds(ours, winner, lot, requirements, cells)
            if grounds:
                deadline, basis = _appeal_deadline(law, protocol_at)
                sections["fas"].append(
                    b._row(
                        f"fas-appeal-result{suffix}",
                        "fas_appeal",
                        "Проверить возможность обжалования итогов" + title_suffix,
                        {
                            "contested": "Решение комиссии: " + (
                                "отклонение нашей заявки" if ours.get("admission") == "REJECTED" else "итоги определения победителя"
                            ),
                            "grounds": "\n".join(grounds),
                            "supporting_documents": "; ".join(
                                filter(None, [lot.get("protocol_document"), "матрица соответствия СОВЫ", "наша заявка"])
                            ),
                            "deadline": _fmt(deadline),
                            "deadline_basis": basis,
                            "decision": "Обжаловать ли — решает тендерный отдел",
                        },
                        evidence=evidence,
                        status=b.STATUS_REVIEW,
                    )
                )

        # п. 89: обжалование, приостановка, запрет на заключение.
        if our_result == "WIN":
            _restriction_row(db, tender, number, suffix, title_suffix, protocol_at, payload, sections)


def _appeal_grounds(
    ours: dict[str, Any], winner: dict[str, Any] | None, lot: dict[str, Any], requirements: list[Requirement], cells: dict
) -> list[str]:
    grounds: list[str] = []
    reason = ours.get("rejection_reason") or ""
    if ours.get("admission") == "REJECTED":
        if not reason:
            grounds.append("Причина отклонения в протоколе не указана")
        elif not _CLAUSE_RE.search(reason):
            grounds.append("В причине отклонения нет ссылки на пункт документации")
        if ours.get("rejection_category") == "TECHNICAL":
            reason_words = _words(reason)
            met = [
                r for r in requirements
                if (cell := cells.get(r.id)) and cell[0].status == ComplianceStatus.MEETS.value
                and len(reason_words & _words(r.text)) >= 2
            ]
            if met:
                grounds.append(
                    "По матрице соответствия прибор МИРТЕК отвечает требованиям, названным в причине: "
                    + "; ".join(r.text[:120] for r in met[:5])
                )
    if (
        winner is not None
        and ours.get("price") is not None
        and winner.get("price") is not None
        and float(ours["price"]) < float(winner["price"])
        and ours.get("admission") != "REJECTED"
    ):
        grounds.append(
            f"Наша цена ({ours['price']}) ниже цены победителя ({winner['price']}) — проверить оценку по критериям"
        )
    if lot.get("conflicts"):
        grounds.append("Протоколы противоречат друг другу: " + "; ".join(lot["conflicts"]))
    return grounds


def _appeal_deadline(law: str | None, protocol_at: datetime | date | None) -> tuple[date | None, str]:
    if protocol_at is None:
        return None, "Дата размещения итогового протокола неизвестна — срок не рассчитан"
    start = protocol_at.date() if isinstance(protocol_at, datetime) else protocol_at
    if law == "44":
        return start + timedelta(days=5), "44-ФЗ, ч. 4 ст. 105: не позднее 5 дней с даты размещения протокола (проверить юристу)"
    if law == "223":
        return start + timedelta(days=10), "135-ФЗ, ч. 4 ст. 18.1: не позднее 10 дней со дня размещения итогов (проверить юристу)"
    return None, "Закон закупки по номеру не определён — срок не рассчитан"


def _condition(conditions: list[dict[str, Any]], kind: str, lot: str) -> dict[str, Any] | None:
    items = [c for c in conditions if c.get("kind") == kind and c.get("presence") != "NOT_REQUIRED"]
    return next((c for c in items if (c.get("lot_number") or "1") == lot), items[0] if items else None)


def _after_win_rows(
    db: Session,
    tender: Tender,
    number: str,
    suffix: str,
    title_suffix: str,
    protocol_at: datetime | date | None,
    payload: dict | None,
    conditions: list[dict[str, Any]],
    sections: Sections,
) -> None:
    """п. 91 — предельная дата подписания, п. 92 — от какого события считается срок поставки
    и, если договор уже размещён, расчётная дата поставки."""

    b = _b()
    signing = _condition(conditions, "SIGNING_DEADLINE", number)
    if signing:
        days = term_days(signing.get("term") or signing.get("value"))
        working = signing.get("day_type") == "WORKING"
        trigger = (signing.get("trigger_event") or signing.get("value") or "").lower()
        start = protocol_at.date() if isinstance(protocol_at, datetime) else protocol_at
        computed = _add_days(start, days, working) if days and start and "протокол" in trigger else None
        sections["contract"].append(
            b._row(
                f"signing-deadline{suffix}",
                "obligation",
                "Предельная дата подписания договора" + title_suffix,
                {
                    "action": "Подписать договор",
                    "trigger_event": signing.get("trigger_event"),
                    "term": signing.get("term") or signing.get("value"),
                    "day_type": b._DAY_TYPE_LABELS.get(signing.get("day_type") or ""),
                    "deadline": _fmt(computed),
                    "basis": (
                        f"Размещение итогового протокола {_fmt(protocol_at)} + {days} "
                        + ("рабочих (без учёта праздников)" if working else "календарных") + " дней"
                    ) if computed else "Дата не рассчитана: нет даты протокола, числа дней или событие-основание другое",
                },
                evidence=b._evidence(signing.get("document"), signing.get("quote")),
                status=b.STATUS_REVIEW,
            )
        )
    delivery = _condition(conditions, "DELIVERY_TERM", number)
    if delivery:
        contracts = [d for d in eis_card_tables.card_documents(payload, "contracts") if d.active and d.placed_at]
        signed_at = min((d.placed_at for d in contracts), default=None)
        days = term_days(delivery.get("term") or delivery.get("value"))
        working = delivery.get("day_type") == "WORKING"
        trigger = (delivery.get("trigger_event") or "").lower()
        computed = (
            _add_days(signed_at.date(), days, working)
            if signed_at and days and re.search(r"заключ|подписан", trigger) else None
        )
        sections["contract"].append(
            b._row(
                f"delivery-start{suffix}",
                "obligation",
                "Срок поставки по договору: событие-основание" + title_suffix,
                {
                    "action": "Поставить товар",
                    "trigger_event": delivery.get("trigger_event") or "Событие-основание в документации не указано",
                    "term": delivery.get("term") or delivery.get("value"),
                    "day_type": b._DAY_TYPE_LABELS.get(delivery.get("day_type") or ""),
                    "contract_published": _fmt(signed_at),
                    "deadline": _fmt(computed),
                    "basis": (
                        "От размещения сведений о договоре в ЕИС (дата подписания может быть раньше)"
                        if computed else None
                    ),
                },
                evidence=b._evidence(delivery.get("document"), delivery.get("quote")),
                status=b.STATUS_REVIEW,
            )
        )


def _restriction_row(
    db: Session,
    tender: Tender,
    number: str,
    suffix: str,
    title_suffix: str,
    protocol_at: datetime | date | None,
    payload: dict | None,
    sections: Sections,
) -> None:
    from app.services import tender_twins

    b = _b()
    stage = eis_card_tables.official_stage(payload)
    ids = [tender.id, *(t.id for t in tender_twins.twins(db, tender))]
    blocking = db.scalars(
        select(TenderEvent)
        .where(
            TenderEvent.tender_id.in_(ids),
            (TenderEvent.event_type == "PROCEDURE_SUSPENDED") | TenderEvent.field_key.like("fas:%"),
        )
        .order_by(TenderEvent.detected_at.desc())
    ).all()
    found = [f"{e.title} ({_fmt(e.effective_at or e.detected_at)})" for e in blocking[:5]]
    if eis_card_tables.is_suspended(stage):
        found.insert(0, f"Этап закупки в ЕИС: {stage}")
    known = payload is not None
    sections["contract"].append(
        b._row(
            f"signing-restrictions{suffix}",
            "obligation",
            "Обжалование, приостановка, запрет на заключение" + title_suffix,
            {
                "result": (
                    "Есть ограничения — проверить до подписания" if found
                    else "По карточке и журналу ЕИС ограничений не найдено" if known
                    else "Нет данных карточки ЕИС — проверить вручную"
                ),
                "details": "\n".join(found) or None,
                "stage": stage,
                "note": "Жалобы проверяются по журналу событий ЕИС; реестр жалоб ФАС не опрашивается",
            },
            status=b.STATUS_REVIEW if found or not known else b.STATUS_EXTRACTED,
        )
    )


# --- пп. 14, 90: редакция проекта договора и сверка окончательного договора ---------------------


def contract_draft_rows(db: Session, tender: Tender, sections: Sections) -> None:
    """Текущая редакция проекта договора: дата публикации, дата изменения, признак «первичный /
    изменённый» (п. 14). Дата публикации файла площадка не отдаёт — берётся дата размещения
    извещения или его последнего изменения из карточки ЕИС, иначе дата обнаружения СОВОЙ.
    После победы — сверка опубликованного договора с проектом по тексту (п. 90)."""

    from app.models.tender_document import DocumentClass, TenderDocument
    from app.services import tender_twins

    b = _b()
    ids = [tender.id, *(t.id for t in tender_twins.twins(db, tender))]
    contracts = db.scalars(
        select(TenderDocument)
        .where(TenderDocument.tender_id.in_(ids), TenderDocument.document_class == DocumentClass.CONTRACT.value)
        .order_by(TenderDocument.created_at)
    ).all()
    if not contracts:
        return
    current = contracts[-1]
    changes = db.scalars(
        select(TenderEvent)
        .where(TenderEvent.tender_id.in_(ids), TenderEvent.event_type == "CONTRACT_CHANGED", TenderEvent.field_key.like("doc:%"))
        .order_by(TenderEvent.detected_at)
    ).all()
    payload = _card(db, tender)
    notice_changes = [
        d for d in eis_card_tables.card_documents(payload, "changes")
        if "изменени" in d.name.lower() and d.placed_at
    ]
    changed_at = max((d.placed_at for d in notice_changes), default=None)
    if changes:
        last = changes[-1]
        changed_at = last.effective_at or changed_at or last.detected_at
    edited = bool(changes) or len(contracts) > 1 or bool(notice_changes)
    sections["contract"].append(
        b._row(
            "contract-draft-edition",
            "document_change",
            "Проект договора: текущая редакция",
            {
                "document": current.file_name,
                "published": _fmt(tender.publish_date),
                "changed": _fmt(changed_at) if edited else None,
                "edition": "Изменённая редакция" if edited else "Первичная редакция",
                "editions_found": len(contracts),
                "basis": "Дата публикации — дата размещения извещения; дата изменения — из журнала изменений "
                         "и карточки ЕИС (изменение извещения могло не касаться договора)",
            },
            evidence=b._evidence(current.file_name, None),
            status=b.STATUS_EXTRACTED if not edited or changes else b.STATUS_REVIEW,
        )
    )
    compared = [e for e in changes if (e.data or {}).get("changes")]
    if compared:
        last = compared[-1]
        data = last.data or {}
        sections["contract"].append(
            b._row(
                "contract-final-vs-draft",
                "document_change",
                "Договор: отличия от прежней редакции",
                {
                    "document": last.source_document,
                    "compared_with": data.get("compared_with"),
                    "fragments_changed": len(data.get("changes") or []),
                    "changes": "\n".join(
                        f"{c.get('field')}: «{(c.get('old_value') or '—')[:200]}» → «{(c.get('new_value') or '—')[:200]}»"
                        for c in (data.get("changes") or [])[:10]
                    ),
                    "note": last.note,
                },
                evidence=b._evidence(last.source_document, None),
                status=b.STATUS_REVIEW,
            )
        )


def extra_rows(
    db: Session,
    tender: Tender,
    requirements: list[Requirement],
    documents: dict,
    cells: dict,
    lock: "BrandLock | None",
    sections: Sections,
) -> None:
    """Все дополнительные строки; сбой одного блока не отменяет остальные и пакет."""

    from loguru import logger

    for label, step in (
        ("лоты", lambda: lot_rows(db, tender, sections)),
        ("история заказчика", lambda: customer_history_rows(db, tender, sections)),
        ("разъяснения", lambda: clarification_rows(db, tender, requirements, documents, lock, sections)),
        ("итоги", lambda: outcome_rows(db, tender, requirements, cells, sections)),
        ("ФАС", lambda: brand_lock_rows(db, tender, lock, sections)),
        ("журнал ФАС", lambda: fas_event_rows(db, tender, sections)),
        ("проект договора", lambda: contract_draft_rows(db, tender, sections)),
    ):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - дополнительная строка не должна ломать пакет
            logger.warning(f"Bitrix24, {tender.external_id}: строки «{label}» не собраны — {exc}")
