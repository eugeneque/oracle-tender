"""Согласие и исправление вердиктов матрицы соответствия (23.10.2026, раздел 70 ARCHITECTURE.md).

Чем это отличается от ответов на заключение ИИ (`ai_feedback_service`): те замечания живут
только в своей закупке и уходят модели текстом при её пересмотре. Исправление вердикта
привязано к паре «требование × производитель», имеет верный статус и причину и поэтому
работает дважды:

* в своей закупке — перекрывает ответ модели сразу и при каждом пересчёте (`overrides`);
* в чужих — прецедентом в запросе к модели, если требование похоже (`precedents_block`).

Согласие ничего не меняет: оно записывается, чтобы было видно, что вердикт проверен, и
чтобы по согласиям и исправлениям можно было мерить точность модели.
"""

from __future__ import annotations

import math
import re
import uuid
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceSource,
    ComplianceStatus,
    Requirement,
    RequirementKind,
)
from app.models.compliance_review import ComplianceVerdictReview, ReviewKind, ReviewReason
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender
from app.models.user import User, UserRole
from app.services import compliance_service
from app.services.audit import log_action
from app.services.lot_position import position_key

MIN_COMMENT_LENGTH = 5
MAX_COMMENT_LENGTH = 2000

# Прецеденты: сколько исправлений положить в один запрос и насколько требование должно быть
# похоже. Сходство — косинус по основам слов (`compliance_service._stems`): 0,5 отсекает
# «общие слова про прибор учёта» и оставляет одно и то же требование в разных редакциях.
MAX_PRECEDENTS = 5
PRECEDENT_MIN_SIMILARITY = 0.5
# Сколько последних исправлений перебирать: сравнение идёт в Python, и тысяча — с запасом
# на годы работы отдела при ничтожной цене одного запроса.
PRECEDENT_POOL = 1000
PRECEDENT_TEXT_CHARS = 300

STATUS_LABELS = {
    ComplianceStatus.MEETS.value: "соответствует",
    ComplianceStatus.PARTIAL.value: "частично",
    ComplianceStatus.NOT_MEETS.value: "не соответствует",
    ComplianceStatus.NO_DATA.value: "нет данных",
}

REASON_LABELS = {
    ReviewReason.FACT.value: "неверный факт о приборе",
    ReviewReason.REQUIREMENT.value: "неверно понято требование",
    ReviewReason.MODEL.value: "сравнили не с той моделью",
    ReviewReason.OTHER.value: "другое",
}


class ReviewError(ValueError):
    """Оценка не принята — текст показывается пользователю как есть."""


def requirement_key(requirement: Requirement) -> str:
    """Ключ требования внутри закупки: формулировка без регистра и лишних пробелов плюс
    позиция спецификации — так же повторный анализ отличает требование от повтора."""

    text = re.sub(r"\s+", " ", (requirement.text or "").lower()).strip()
    return f"{text} | {position_key(requirement.position) or ''}"


def _current(db: Session, tender_id: uuid.UUID, requirement_id: uuid.UUID, manufacturer_id: uuid.UUID):
    return list(
        db.scalars(
            select(ComplianceVerdictReview).where(
                ComplianceVerdictReview.tender_id == tender_id,
                ComplianceVerdictReview.requirement_id == requirement_id,
                ComplianceVerdictReview.manufacturer_id == manufacturer_id,
                ComplianceVerdictReview.is_current.is_(True),
            )
        )
    )


def create(
    db: Session,
    tender: Tender,
    *,
    requirement_id: uuid.UUID,
    manufacturer_id: uuid.UUID,
    kind: str,
    corrected_status: str | None,
    reason: str | None,
    comment: str | None,
    actor: User,
) -> ComplianceVerdictReview:
    if kind not in {item.value for item in ReviewKind}:
        raise ReviewError("Неизвестный вид оценки")
    requirement = db.get(Requirement, requirement_id)
    if (
        requirement is None
        or requirement.tender_id != tender.id
        or requirement.kind != RequirementKind.PRODUCT.value
    ):
        raise ReviewError("Требование не найдено в матрице этой закупки — обновите карточку")
    manufacturer = db.get(Manufacturer, manufacturer_id)
    entry = db.scalar(
        select(ComplianceMatrixEntry).where(
            ComplianceMatrixEntry.requirement_id == requirement_id,
            ComplianceMatrixEntry.manufacturer_id == manufacturer_id,
        )
    )
    if manufacturer is None or entry is None:
        raise ReviewError("Вердикта по этому производителю нет — дождитесь расчёта матрицы")

    cleaned = (comment or "").strip()
    if len(cleaned) > MAX_COMMENT_LENGTH:
        raise ReviewError(f"Комментарий длиннее {MAX_COMMENT_LENGTH} символов — сократите его")

    current = _current(db, tender.id, requirement_id, manufacturer_id)
    correction = next((item for item in current if item.kind == ReviewKind.DISAGREE.value), None)
    if kind == ReviewKind.DISAGREE.value:
        if corrected_status not in STATUS_LABELS:
            raise ReviewError("Выберите верный вердикт")
        if reason not in REASON_LABELS:
            raise ReviewError("Выберите причину ошибки")
        if len(cleaned) < MIN_COMMENT_LENGTH:
            raise ReviewError(
                "Напишите, в чём ошибка — этот текст увидит модель в похожих закупках"
            )
        if corrected_status == entry.status:
            raise ReviewError(
                f"Вердикт и так «{STATUS_LABELS[corrected_status]}» — нажмите «Согласен»"
            )
        # Новое исправление заменяет прежние оценки ячейки: согласия относились к вердикту,
        # которого больше нет.
        for item in current:
            item.is_current = False
    else:
        corrected_status = None
        reason = None
        for item in current:
            if item.kind == ReviewKind.AGREE.value and item.user_id == actor.id:
                item.is_current = False

    # Снимок ответа модели. Если ячейка уже исправлена, модель «сказала» то, что записано в
    # прежнем исправлении, — иначе снимок показал бы чужое исправление как ответ модели.
    if correction is not None:
        model_status = correction.model_status
        model_explanation = correction.model_explanation
        model_source = correction.model_source
        model_confidence = correction.model_confidence
    else:
        model_status = entry.status
        model_explanation = entry.explanation
        model_source = entry.source
        model_confidence = entry.confidence

    review = ComplianceVerdictReview(
        tender_id=tender.id,
        requirement_id=requirement.id,
        manufacturer_id=manufacturer.id,
        user_id=actor.id,
        kind=kind,
        corrected_status=corrected_status,
        reason=reason,
        comment=cleaned or None,
        requirement_text=requirement.text or "",
        requirement_key=requirement_key(requirement),
        model_status=model_status,
        model_explanation=model_explanation,
        model_source=model_source,
        model_confidence=model_confidence,
        is_current=True,
    )
    db.add(review)

    if kind == ReviewKind.DISAGREE.value:
        entry.status = corrected_status
        entry.explanation = cleaned
        entry.source = ComplianceSource.HUMAN_REVIEW.value
        entry.confidence = Decimal("1.000")
        entry.needs_human_review = False
        db.flush()
        compliance_service.recalculate_manufacturer(db, tender, manufacturer)

    who = manufacturer.brand_name or manufacturer.legal_name
    log_action(
        db,
        component="compliance_review",
        action=f"{kind}:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"{who}: «{(requirement.text or '')[:200]}» — "
            + (
                f"{STATUS_LABELS.get(model_status or '', model_status)} → "
                f"{STATUS_LABELS[corrected_status]} ({REASON_LABELS[reason]}): {cleaned[:300]}"
                if kind == ReviewKind.DISAGREE.value
                else f"согласие с «{STATUS_LABELS.get(entry.status, entry.status)}»"
            )
        ),
        user_id=actor.id,
    )
    db.commit()
    db.refresh(review)
    return review


def withdraw(db: Session, tender: Tender, review_id: uuid.UUID, actor: User) -> None:
    """Отзыв своей оценки (администратор — любой). Отозванное исправление возвращает ячейке
    ответ модели из снимка и перестаёт быть прецедентом."""

    review = db.get(ComplianceVerdictReview, review_id)
    if review is None or review.tender_id != tender.id or not review.is_current:
        raise ReviewError("Оценка не найдена или уже отозвана — обновите карточку")
    if review.user_id != actor.id and actor.role != UserRole.ADMIN.value:
        raise ReviewError("Отозвать можно только свою оценку")
    review.is_current = False

    manufacturer = db.get(Manufacturer, review.manufacturer_id)
    if review.kind == ReviewKind.DISAGREE.value and review.requirement_id is not None:
        entry = db.scalar(
            select(ComplianceMatrixEntry).where(
                ComplianceMatrixEntry.requirement_id == review.requirement_id,
                ComplianceMatrixEntry.manufacturer_id == review.manufacturer_id,
            )
        )
        if entry is not None and entry.source == ComplianceSource.HUMAN_REVIEW.value:
            confidence = float(review.model_confidence or 0)
            entry.status = review.model_status or ComplianceStatus.NO_DATA.value
            entry.explanation = review.model_explanation
            entry.source = review.model_source or ComplianceSource.AI_SEMANTIC.value
            entry.confidence = review.model_confidence
            entry.needs_human_review = (
                confidence < compliance_service.LOW_CONFIDENCE_THRESHOLD
                and entry.status != ComplianceStatus.NO_DATA.value
            )
        # Согласия с исправлением относились к нему — отзываются вместе с ним.
        for item in _current(db, tender.id, review.requirement_id, review.manufacturer_id):
            item.is_current = False
        db.flush()
        if manufacturer is not None:
            compliance_service.recalculate_manufacturer(db, tender, manufacturer)

    log_action(
        db,
        component="compliance_review",
        action=f"withdraw:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=f"{review.kind}: «{review.requirement_text[:200]}»",
        user_id=actor.id,
    )
    db.commit()


def overrides(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
) -> dict[int, ComplianceVerdictReview]:
    """Действующие исправления производителя в закупке — по номеру требования (с единицы).

    Требование ищется по ссылке, а после повторного анализа документации (ссылка обнулена)
    — по формулировке; найденное по формулировке исправление привязывается к новому
    требованию, чтобы ячейка снова показывала, кто и что исправил."""

    reviews = list(
        db.scalars(
            select(ComplianceVerdictReview)
            .where(
                ComplianceVerdictReview.tender_id == tender.id,
                ComplianceVerdictReview.manufacturer_id == manufacturer.id,
                ComplianceVerdictReview.kind == ReviewKind.DISAGREE.value,
                ComplianceVerdictReview.is_current.is_(True),
            )
            .order_by(ComplianceVerdictReview.created_at)
        )
    )
    if not reviews:
        return {}
    by_id = {item.id: index for index, item in enumerate(requirements, start=1)}
    by_key = {requirement_key(item): index for index, item in enumerate(requirements, start=1)}
    result: dict[int, ComplianceVerdictReview] = {}
    for review in reviews:
        index = by_id.get(review.requirement_id) if review.requirement_id else None
        if index is None:
            index = by_key.get(review.requirement_key)
            if index is None:
                continue
            review.requirement_id = requirements[index - 1].id
        result[index] = review  # позднее исправление — главнее
    return result


def _similarity(first: frozenset[str], second: frozenset[str]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / math.sqrt(len(first) * len(second))


def find_precedents(
    db: Session,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    *,
    tender_id: uuid.UUID | None = None,
) -> list[tuple[ComplianceVerdictReview, str]]:
    """Исправления по похожим требованиям — с именем производителя, к которому относятся.

    Берутся исправления этого производителя и исправления «неверно понято требование» любых
    производителей: ошибка трактовки не зависит от прибора, а неверный факт о чужом приборе
    к этому производителю отношения не имеет. Исправления тех же требований этой закупки по
    этому же производителю не берутся — они и так перекрывают ответ (`overrides`)."""

    if not requirements:
        return []
    rows = db.execute(
        select(ComplianceVerdictReview, Manufacturer.brand_name, Manufacturer.legal_name)
        .join(Manufacturer, Manufacturer.id == ComplianceVerdictReview.manufacturer_id)
        .where(
            ComplianceVerdictReview.kind == ReviewKind.DISAGREE.value,
            ComplianceVerdictReview.is_current.is_(True),
            or_(
                ComplianceVerdictReview.manufacturer_id == manufacturer.id,
                ComplianceVerdictReview.reason == ReviewReason.REQUIREMENT.value,
            ),
        )
        .order_by(ComplianceVerdictReview.created_at.desc())
        .limit(PRECEDENT_POOL)
    ).all()
    if not rows:
        return []

    own_keys = {requirement_key(item) for item in requirements}
    targets = [
        compliance_service._stems(f"{item.text or ''} {item.normalized_text or ''}")
        for item in requirements
    ]
    scored: list[tuple[float, ComplianceVerdictReview, str]] = []
    seen: set[tuple[str, uuid.UUID, str | None]] = set()
    for review, brand, legal in rows:
        if (
            review.tender_id == tender_id
            and review.manufacturer_id == manufacturer.id
            and review.requirement_key in own_keys
        ):
            continue
        # Одно и то же исправление, повторённое в разных закупках, — один прецедент.
        dedup = (review.requirement_key.rsplit(" | ", 1)[0], review.manufacturer_id, review.corrected_status)
        if dedup in seen:
            continue
        stems = compliance_service._stems(review.requirement_text)
        best = max(_similarity(stems, target) for target in targets)
        if best < PRECEDENT_MIN_SIMILARITY:
            continue
        seen.add(dedup)
        scored.append((best, review, brand or legal))
    scored.sort(key=lambda item: -item[0])
    return [(review, name) for _, review, name in scored[:MAX_PRECEDENTS]]


def precedents_block(
    db: Session,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    *,
    tender_id: uuid.UUID | None = None,
) -> tuple[str | None, int]:
    """Раздел запроса к модели с прецедентами и их число (для журнала расчёта)."""

    found = find_precedents(db, manufacturer, requirements, tender_id=tender_id)
    if not found:
        return None, 0
    lines = ["ИСПРАВЛЕНИЯ СПЕЦИАЛИСТОВ ПО ПОХОЖИМ ТРЕБОВАНИЯМ ДРУГИХ ЗАКУПОК:"]
    for review, name in found:
        said = STATUS_LABELS.get(review.model_status or "", review.model_status or "—")
        explanation = (review.model_explanation or "").strip()
        lines.append(
            f"- Требование: «{review.requirement_text[:PRECEDENT_TEXT_CHARS]}». "
            f"Производитель: {name}. Модель ответила «{said}»"
            + (f" ({explanation[:200]})" if explanation else "")
            + f". Специалист исправил на «{STATUS_LABELS[review.corrected_status]}», "
            f"причина — {REASON_LABELS.get(review.reason or '', 'другое')}: {review.comment}"
        )
    return "\n".join(lines), len(found)


def serialize(review: ComplianceVerdictReview, user_name: str | None) -> dict:
    return {
        "id": review.id,
        "requirement_id": review.requirement_id,
        "manufacturer_id": review.manufacturer_id,
        "user_id": review.user_id,
        "user_name": user_name,
        "kind": review.kind,
        "corrected_status": review.corrected_status,
        "reason": review.reason,
        "comment": review.comment,
        "model_status": review.model_status,
        "model_explanation": review.model_explanation,
        "created_at": review.created_at,
    }


def current_for_tender(db: Session, tender_id: uuid.UUID) -> dict[tuple[uuid.UUID, uuid.UUID], list[dict]]:
    """Действующие оценки закупки по ячейкам — для матрицы в карточке."""

    rows = db.execute(
        select(ComplianceVerdictReview, User.full_name)
        .outerjoin(User, User.id == ComplianceVerdictReview.user_id)
        .where(
            ComplianceVerdictReview.tender_id == tender_id,
            ComplianceVerdictReview.is_current.is_(True),
            ComplianceVerdictReview.requirement_id.is_not(None),
        )
        .order_by(ComplianceVerdictReview.created_at)
    ).all()
    result: dict[tuple[uuid.UUID, uuid.UUID], list[dict]] = {}
    for review, name in rows:
        result.setdefault((review.requirement_id, review.manufacturer_id), []).append(
            serialize(review, name)
        )
    return result
