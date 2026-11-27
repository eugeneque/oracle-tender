"""Справочник приборов: источник характеристик, доказательства и разбор описания типа (27.11.2026).

Значение остаётся в обычных полях модификации, а здесь хранится его происхождение. Ручное
решение не перезаписывается; скрипт имеет приоритет над ещё не подтверждённым ответом ИИ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.fgis import FgisAdapter
from app.models.meter_market import (
    AttributeReviewStatus,
    AttributeSource,
    MeterMarketAttributeReview,
    MeterMarketModification,
    MeterMarketType,
)
from app.services import meter_market_interface as interface_rules

TRACKED_FIELDS = ("phase", "design", "interface", "mpi_years", "smart_890")
UNKNOWN_VALUES = {None, "", "nd", "н/д", "нет данных"}


@dataclass(frozen=True)
class AttributeProposal:
    value: Any
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "high"


def _known(value: Any) -> bool:
    return value not in UNKNOWN_VALUES


def _review(db: Session, modification_id: int, field_name: str) -> MeterMarketAttributeReview | None:
    return db.scalar(
        select(MeterMarketAttributeReview).where(
            MeterMarketAttributeReview.modification_id == modification_id,
            MeterMarketAttributeReview.field_name == field_name,
        )
    )


def apply_attribute(
    db: Session,
    modification: MeterMarketModification,
    field_name: str,
    value: Any,
    *,
    source: str,
    status: str,
    reason: str = "",
    evidence: list[dict[str, Any]] | None = None,
    confidence: str | None = None,
    only_if_unknown: bool = False,
    force: bool = False,
) -> bool:
    """Записать значение и его происхождение. Возвращает, изменилось ли значение/метаданные."""

    if field_name not in TRACKED_FIELDS:
        raise ValueError(f"Неизвестная характеристика: {field_name}")
    if modification.id is None:
        db.add(modification)
        db.flush()
    review = _review(db, modification.id, field_name)
    if not force and review is not None and review.source == AttributeSource.MANUAL.value:
        return False
    current = getattr(modification, field_name)
    pending_ai = (
        review is not None
        and review.source == AttributeSource.AI.value
        and review.status == AttributeReviewStatus.PENDING.value
    )
    if only_if_unknown and _known(current) and not pending_ai:
        return False
    changed = current != value
    if changed:
        setattr(modification, field_name, value)
        if field_name == "smart_890":
            modification.smart_890_source = source
            modification.smart_890_note = reason[:500] or None
            modification.smart_890_at = datetime.now(timezone.utc)
    if review is None:
        review = MeterMarketAttributeReview(modification_id=modification.id, field_name=field_name)
        db.add(review)
    metadata = (source, status, confidence or None, reason or None, evidence or [])
    old_metadata = (review.source, review.status, review.confidence, review.reason, review.evidence)
    review.source = source
    review.status = status
    review.confidence = confidence or None
    review.reason = reason or None
    review.evidence = evidence or []
    review.updated_at = datetime.now(timezone.utc)
    db.flush()
    return changed or metadata != old_metadata


def mark_unresolved(
    db: Session,
    modification: MeterMarketModification,
    field_name: str,
    *,
    source: str,
    reason: str,
    confidence: str | None = None,
) -> bool:
    current = getattr(modification, field_name)
    review = _review(db, modification.id, field_name)
    if review is not None and review.source == AttributeSource.MANUAL.value:
        return False
    if _known(current) and (review is None or review.status != AttributeReviewStatus.PENDING.value):
        return False
    unknown = "nd" if field_name == "design" else None
    return apply_attribute(
        db,
        modification,
        field_name,
        unknown,
        source=source,
        status=AttributeReviewStatus.UNRESOLVED.value,
        reason=reason,
        confidence=confidence,
        force=True,
    )


def attributes_for(db: Session, modification_ids: list[int]) -> dict[int, dict[str, dict[str, Any]]]:
    if not modification_ids:
        return {}
    rows = db.scalars(
        select(MeterMarketAttributeReview).where(MeterMarketAttributeReview.modification_id.in_(modification_ids))
    ).all()
    result: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row.modification_id, {})[row.field_name] = {
            "source": row.source,
            "status": row.status,
            "confidence": row.confidence,
            "reason": row.reason,
            "evidence": row.evidence or [],
            "updated_at": row.updated_at,
        }
    return result


def reset_attribute(db: Session, modification: MeterMarketModification, field_name: str) -> bool:
    """Снять ручное решение, чтобы следующий разбор снова мог заполнить поле."""

    review = _review(db, modification.id, field_name)
    if review is None or review.source != AttributeSource.MANUAL.value:
        return False
    db.delete(review)
    db.flush()
    return True


def confirm_attribute(db: Session, modification: MeterMarketModification, field_name: str) -> bool:
    review = _review(db, modification.id, field_name)
    if review is None or review.status != AttributeReviewStatus.PENDING.value:
        return False
    review.source = AttributeSource.MANUAL.value
    review.status = AttributeReviewStatus.CONFIRMED.value
    review.updated_at = datetime.now(timezone.utc)
    if field_name == "smart_890":
        modification.smart_890_source = AttributeSource.MANUAL.value
    db.flush()
    return True


def reject_attribute(db: Session, modification: MeterMarketModification, field_name: str) -> bool:
    review = _review(db, modification.id, field_name)
    if review is None or review.status != AttributeReviewStatus.PENDING.value:
        return False
    unknown = "nd" if field_name == "design" else None
    reason = f"Предложение ИИ отклонено аналитиком. {review.reason or ''}".strip()
    apply_attribute(
        db,
        modification,
        field_name,
        unknown,
        source=AttributeSource.MANUAL.value,
        status=AttributeReviewStatus.UNRESOLVED.value,
        reason=reason,
        force=True,
    )
    if field_name == "smart_890":
        modification.smart_890_source = AttributeSource.MANUAL.value
        modification.smart_890_note = reason
    db.flush()
    return True


# ── Описание типа и его релевантные разделы ────────────────────────────────────────────────


def description_text(db: Session, meter_type: MeterMarketType) -> str | None:
    if meter_type.description_type_text:
        return meter_type.description_type_text
    return db.execute(
        text(
            "SELECT description_type_text FROM si_types WHERE si_code = :c AND description_type_text IS NOT NULL "
            "ORDER BY length(description_type_text) DESC LIMIT 1"
        ),
        {"c": meter_type.mit_number},
    ).scalar()


def refresh_description(db: Session, meter_type: MeterMarketType, *, adapter: FgisAdapter | None = None) -> str:
    row = db.execute(
        text(
            "SELECT description_type_url, description_type_mirror_url, description_type_version FROM si_types "
            "WHERE si_code = :c ORDER BY length(description_type_text) DESC NULLS LAST LIMIT 1"
        ),
        {"c": meter_type.mit_number},
    ).first()
    url = meter_type.description_type_url or (row[0] if row else None) or ""
    mirror = meter_type.description_type_mirror_url or (row[1] if row else None)
    if not url and not mirror:
        raise ValueError(f"У типа {meter_type.mit_number} нет ссылки на «Описание типа»")
    adapter = adapter or FgisAdapter()
    extracted = adapter.fetch_description_type_text(url, mirror_url=mirror)
    if not extracted:
        raise ValueError(f"«Описание типа» {meter_type.mit_number} не загрузилось ни из ФГИС, ни из зеркала")
    meter_type.description_type_url = url or None
    meter_type.description_type_mirror_url = mirror
    meter_type.description_type_version = meter_type.description_type_version or (row[2] if row else None)
    meter_type.description_type_text = extracted
    meter_type.description_type_fetched_at = datetime.now(timezone.utc)
    db.flush()
    return extracted


_STRUCTURE = re.compile(r"структур\w*\s+(?:условного\s+)?обозначени|условн\w*\s+обозначени\w*\s+счетчик", re.I)
_RELEVANT = re.compile(
    r"основной\s+интерфейс|дополнительные\s+интерфейсы|наличие\s+(?:дополнительных\s+)?интерфейсов\s+связи|"
    r"технические\s+характеристики|тип\s+корпуса",
    re.I,
)
_DESCRIPTION_HEAD = re.compile(r"описание\s+средства\s+измерени", re.I)


def relevant_description(description: str, *, max_chars: int = 30_000) -> str:
    """Все разнесённые по документу блоки, нужные для расшифровки обозначения.

    У ФОБОС структура корпуса и таблица интерфейсов находятся на пяти листах друг от друга;
    одно окно после слова «структура» поэтому теряло G(n).
    """

    head = _DESCRIPTION_HEAD.search(description)
    start = head.start() if head else 0
    windows = [(start, min(len(description), start + 2500))]
    for match in list(_STRUCTURE.finditer(description)) + list(_RELEVANT.finditer(description)):
        begin = max(start, match.start() - 500)
        windows.append((begin, min(len(description), match.start() + 7000)))
    merged: list[list[int]] = []
    for begin, end in sorted(windows):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    result = "\n…\n".join(description[begin:end] for begin, end in merged)
    return result[:max_chars]


def _page_at(description: str, position: int) -> str | None:
    pages = list(re.finditer(r"Лист\s+№\s*(\d+)", description[:position], re.I))
    return pages[-1].group(1) if pages else None


def _evidence(description: str, *, code: str, quote: str, anchor: str | None = None) -> list[dict[str, Any]]:
    position = description.find(anchor or quote)
    if position < 0:
        position = description.find(quote[:80])
    return [{
        "code": code,
        "page": _page_at(description, max(position, 0)),
        "quote": quote.strip()[:1200],
    }]


# ── Детерминированная расшифровка ──────────────────────────────────────────────────────────


def _phase(description: str) -> AttributeProposal | None:
    head = description[:4000]
    value = "1ф" if re.search(r"однофазн", head, re.I) else "3ф" if re.search(r"тр[её]хфазн", head, re.I) else None
    if value is None:
        return None
    line = next((line.strip() for line in head.splitlines() if "счетчик" in line.lower() and "фазн" in line.lower()), value)
    return AttributeProposal(value, f"Фазность указана в наименовании типа: {line}", _evidence(description, code=value, quote=line))


_DESIGN_LINE = re.compile(
    r"^\s*(?:[–—-]\s*)?([SС][MМ]?\(n\)|[SС][MМ]?\(\d+\)|[MМ]\(n\)|[MМ]\(\d+\)|W\d+[A-Z]?|D\d+[A-Z]?|SP\d+)\s+"
    r"[–—-]\s+(.+)$",
    re.I | re.M,
)


def _design_kind(text: str) -> str | None:
    if re.search(r"DIN|рейк", text, re.I):
        return "din"
    if re.search(r"сплит", text, re.I):
        return "split"
    if re.search(r"шкаф|щиток|винт", text, re.I):
        return "cabinet"
    return None


def _design(raw: str, description: str) -> AttributeProposal | None:
    normalized = raw.upper().translate(interface_rules._CYR_TO_LAT)
    for match in _DESIGN_LINE.finditer(description):
        documented, explanation = match.groups()
        kind = _design_kind(explanation)
        if kind is None:
            continue
        code = documented.upper().translate(interface_rules._CYR_TO_LAT)
        if "(N)" in code:
            pattern = re.escape(code).replace(r"\(N\)", r"\(\d+\)")
        else:
            pattern = re.escape(code)
        left_boundary = "" if "(" in code else r"(?<![A-Z0-9])"
        found = re.search(left_boundary + pattern + r"(?![A-Z0-9])", normalized)
        if found:
            quote = match.group(0).strip()
            return AttributeProposal(
                kind,
                f"{found.group(0)} — {explanation.strip()}",
                _evidence(description, code=found.group(0), quote=quote),
            )
    return None


def script_attributes(raw: str, description: str) -> dict[str, AttributeProposal]:
    result: dict[str, AttributeProposal] = {}
    if phase := _phase(description):
        result["phase"] = phase
    if design := _design(raw, description):
        result["design"] = design
    table = interface_rules.parse_table(interface_rules.codes_from_description(description))
    if table:
        decision = interface_rules.decode(raw, table)
        if decision.review is None and decision.label and decision.label != interface_rules.NO_INTERFACE:
            label = decision.label
            reason = decision.note
            evidence: list[dict[str, Any]] = []
            for code in decision.codes:
                if "(" in code:
                    letter = code.split("(", 1)[0]
                    line = next((line.strip() for line in description.splitlines() if re.search(re.escape(letter) + r"\((?:1|n)\)", line, re.I)), "")
                else:
                    line = next((line.strip() for line in description.splitlines() if code in line and "интерфейс" in line.lower()), "")
                if line:
                    evidence.extend(_evidence(description, code=code, quote=line))
            if (
                re.search(r"нет\s+символа\s+W\s*[-–—]\s*счетчик\s+c\s+радиомодемом", description, re.I)
                and re.search(r"NB[- ]?Fi", description, re.I)
                and not re.search(r"(?<![A-Z0-9])W(?![A-Z0-9])", raw.upper())
            ):
                label = interface_rules.join_label(interface_rules.split_label(label) + ["NB-Fi"])
                reason += "; нет символа W — встроенный радиомодем NB-Fi"
                line = next(line.strip() for line in description.splitlines() if re.search(r"нет\s+символа\s+W", line, re.I))
                evidence.extend(_evidence(description, code="нет W", quote=line))
            if not evidence:
                evidence = _evidence(description, code=", ".join(decision.codes), quote=reason, anchor="интерфейс")
            result["interface"] = AttributeProposal(label, reason, evidence)
    return result


def apply_script_to_type(db: Session, meter_type: MeterMarketType, description: str | None = None) -> int:
    """Заполнить модификации типа доказанными скриптом значениями, не трогая прежние решения."""

    description = description or description_text(db, meter_type)
    if not description:
        return 0
    changed = 0
    modifications = db.scalars(
        select(MeterMarketModification).where(MeterMarketModification.type_id == meter_type.id)
    ).all()
    for modification in modifications:
        for field_name, proposal in script_attributes(modification.name, description).items():
            changed += apply_attribute(
                db,
                modification,
                field_name,
                proposal.value,
                source=AttributeSource.SCRIPT.value,
                status=AttributeReviewStatus.CONFIRMED.value,
                reason=proposal.reason,
                evidence=proposal.evidence,
                confidence=proposal.confidence,
                only_if_unknown=True,
            )
    db.flush()
    return changed
