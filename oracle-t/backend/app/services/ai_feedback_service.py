"""Согласие и несогласие специалиста с заключением ИИ (28.09.2026).

Согласие просто записывается. Несогласие уходит на пересмотр фоновой задачей
`JobKind.AI_FEEDBACK`: оценка пересчитывается с замечаниями в промптах, и к замечанию
прикладывается заключение до и после. Раздел «Ответы специалистов» в настройках и блок
«Комментарии» в карточке читают эти записи.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.jobs import enqueue
from app.models.ai_feedback import AiScoreFeedback, FeedbackKind, FeedbackStatus
from app.models.job import BackgroundJob, JobKind
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.user import User
from app.services import ai_conclusion_service, ai_profile_service
from app.services.audit import log_action

MIN_TEXT_LENGTH = 5
MAX_TEXT_LENGTH = 4000
# Сколько раз задача перечитывает очередь замечаний: пришедшее во время пересмотра
# обрабатывается тем же проходом, но бесконечно крутиться нельзя.
MAX_ROUNDS = 3


class FeedbackError(ValueError):
    """Замечание не принято — текст показывается пользователю как есть."""


def create(
    db: Session, tender: Tender, *, kind: str, text: str | None, actor: User
) -> tuple[AiScoreFeedback, BackgroundJob | None]:
    if kind not in {item.value for item in FeedbackKind}:
        raise FeedbackError("Неизвестный вид ответа")
    cleaned = (text or "").strip()
    if kind == FeedbackKind.DISAGREE.value and len(cleaned) < MIN_TEXT_LENGTH:
        raise FeedbackError("Напишите, с чем вы не согласны — модель пересмотрит заключение по этому тексту")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise FeedbackError(f"Замечание длиннее {MAX_TEXT_LENGTH} символов — сократите его")

    score = ai_profile_service.get_current(db, tender.id)
    if score is None:
        raise FeedbackError("Заключения ещё нет — дождитесь разбора, затем ответьте на него")

    feedback = AiScoreFeedback(
        tender_id=tender.id,
        user_id=actor.id,
        kind=kind,
        text=cleaned or None,
        status=(
            FeedbackStatus.PENDING.value
            if kind == FeedbackKind.DISAGREE.value
            else FeedbackStatus.RECORDED.value
        ),
        score_before_id=score.id,
        before=ai_conclusion_service.snapshot(score),
    )
    db.add(feedback)
    log_action(
        db,
        component="ai_feedback",
        action=f"{kind}:{tender.external_id}",
        result="queued" if kind == FeedbackKind.DISAGREE.value else "success",
        level=LogLevel.INFO,
        details=cleaned[:500] or None,
        user_id=actor.id,
    )
    db.commit()
    db.refresh(feedback)

    job = None
    if kind == FeedbackKind.DISAGREE.value:
        job = enqueue(db, kind=JobKind.AI_FEEDBACK, tender=tender, actor=actor)
    return feedback, job


def _pending(db: Session, tender_id: uuid.UUID) -> list[AiScoreFeedback]:
    return list(
        db.scalars(
            select(AiScoreFeedback)
            .where(
                AiScoreFeedback.tender_id == tender_id,
                AiScoreFeedback.status.in_(
                    [FeedbackStatus.PENDING.value, FeedbackStatus.PROCESSING.value]
                ),
            )
            .order_by(AiScoreFeedback.created_at)
        )
    )


def resolve_pending(db: Session, tender: Tender, items: list[AiScoreFeedback] | None = None) -> int:
    """Прикладывает к ожидающим замечаниям текущее заключение как «после».

    Вызывается после любого пересчёта оценки, а не только из задачи пересмотра: «Обновить
    разбор» тоже учитывает все замечания, и оставлять их висеть «на пересмотре» было бы
    неправдой.
    """

    items = items if items is not None else _pending(db, tender.id)
    score = ai_profile_service.get_current(db, tender.id)
    if not items or score is None:
        return 0
    after = ai_conclusion_service.snapshot(score)
    response = (score.conclusion or {}).get("feedback_response")
    now = datetime.now(timezone.utc)
    for item in items:
        item.status = FeedbackStatus.APPLIED.value
        item.score_after_id = score.id
        item.after = after
        item.ai_response = response
        item.processed_at = now
        item.error = None
    db.commit()
    return len(items)


def process_pending(db: Session, tender: Tender, actor: User | None) -> str:
    """Тело задачи пересмотра: пересчитывает оценку, пока есть необработанные замечания."""

    # Очередь задач повторяет упавшую задачу (`MAX_ATTEMPTS`): замечания, помеченные ошибкой
    # первой попыткой, возвращаются в работу, иначе повтор нашёл бы пустую очередь и
    # отчитался бы успехом.
    recent = datetime.now(timezone.utc) - timedelta(minutes=15)
    for item in db.scalars(
        select(AiScoreFeedback).where(
            AiScoreFeedback.tender_id == tender.id,
            AiScoreFeedback.status == FeedbackStatus.ERROR.value,
            AiScoreFeedback.created_at >= recent,
        )
    ):
        item.status = FeedbackStatus.PENDING.value
    db.commit()

    processed = 0
    for _ in range(MAX_ROUNDS):
        items = _pending(db, tender.id)
        if not items:
            break
        for item in items:
            item.status = FeedbackStatus.PROCESSING.value
        db.commit()
        try:
            outcome = ai_profile_service.compute_profile_score(db, tender, actor=actor)
        except Exception as exc:  # noqa: BLE001 - ошибка записывается в само замечание
            db.rollback()
            logger.warning(f"Пересмотр заключения {tender.external_id} не удался: {exc}")
            for item in _pending(db, tender.id):
                item.status = FeedbackStatus.ERROR.value
                item.error = str(exc)[:1000]
                item.processed_at = datetime.now(timezone.utc)
            db.commit()
            raise
        processed += resolve_pending(db, tender, items)
        if outcome.messages:
            logger.info(f"Пересмотр {tender.external_id}: {'; '.join(outcome.messages)}")

    score = ai_profile_service.get_current(db, tender.id)
    label = (score.conclusion or {}).get("fit_label") if score else None
    return f"Учтено замечаний: {processed}" + (f"; заключение: {label}" if label else "")


# --- чтение ---------------------------------------------------------------------------------


def serialize(item: AiScoreFeedback, user_name: str | None, tender: Tender | None = None) -> dict:
    return {
        "id": item.id,
        "tender_id": item.tender_id,
        "tender_title": tender.title if tender else None,
        "tender_external_id": tender.external_id if tender else None,
        "source_name": tender.source.name if tender and tender.source else None,
        "user_id": item.user_id,
        "user_name": user_name,
        "kind": item.kind,
        "text": item.text,
        "status": item.status,
        "error": item.error,
        "before": item.before,
        "after": item.after,
        "ai_response": item.ai_response,
        "created_at": item.created_at,
        "processed_at": item.processed_at,
    }


def list_for_tender(db: Session, tender: Tender) -> list[dict]:
    rows = db.execute(
        select(AiScoreFeedback, User.full_name)
        .outerjoin(User, User.id == AiScoreFeedback.user_id)
        .where(AiScoreFeedback.tender_id == tender.id)
        .order_by(AiScoreFeedback.created_at.desc())
    ).all()
    return [serialize(item, name, tender) for item, name in rows]


def list_all(
    db: Session, *, kind: str | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    query = select(AiScoreFeedback, User.full_name, Tender).outerjoin(
        User, User.id == AiScoreFeedback.user_id
    ).join(Tender, Tender.id == AiScoreFeedback.tender_id)
    count_query = select(func.count()).select_from(AiScoreFeedback)
    if kind:
        query = query.where(AiScoreFeedback.kind == kind)
        count_query = count_query.where(AiScoreFeedback.kind == kind)
    rows = db.execute(
        query.order_by(AiScoreFeedback.created_at.desc()).limit(limit).offset(offset)
    ).all()
    total = db.scalar(count_query) or 0
    return [serialize(item, name, tender) for item, name, tender in rows], total
