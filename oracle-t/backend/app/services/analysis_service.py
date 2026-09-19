"""Чтение результатов анализа: требования, матрица соответствия, проценты победителя
(разделы 5.5, 5.6 ТЗ — Этапы 5-6).

Отдельно от `tender_analysis` и `compliance_service`: те считают и пишут, этот — читает и
собирает в форму, удобную интерфейсу. Разделение нужно, чтобы карточка тендера открывалась
без побочных эффектов: просмотр не должен запускать обращения к модели.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.ai_profile import AiProfileScore
from app.models.analysis import (
    ComplianceMatrixEntry,
    Criticality,
    Requirement,
    RequirementKind,
    WinPercentage,
)
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender
from app.models.user import User
from app.schemas.tender import (
    ComplianceEntryOut,
    ComplianceMatrixOut,
    RequirementOut,
    WinPercentageOut,
)


def list_requirements(db: Session, tender_id: uuid.UUID) -> list[Requirement]:
    return list(
        db.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender_id)
            # Критичные — первыми: в карточке тендера важен не хронологический порядок
            # извлечения, а то, что решает исход заявки.
            .order_by(_criticality_rank(), Requirement.created_at)
        )
    )


def _criticality_rank():
    """Порядок сортировки по критичности: critical → important → minor.

    Хранится строкой (значения enum), поэтому по алфавиту порядок был бы неверным
    (critical, important, minor — случайно совпадает, но minor < important в алфавите нет),
    и сортировка задаётся явным CASE."""

    return case(
        {
            Criticality.CRITICAL.value: 0,
            Criticality.IMPORTANT.value: 1,
            Criticality.MINOR.value: 2,
        },
        value=Requirement.criticality,
        else_=3,
    )


def get_compliance_matrix(db: Session, tender: Tender) -> ComplianceMatrixOut:
    """Матрица в том виде, в каком её рисует карточка тендера (раздел 5.6 ТЗ)."""

    # В матрице — только требования к товару: только по ним есть ячейки (см.
    # `compliance_service.evaluate_tender`). Полный список — на вкладке «Требования».
    requirements = [
        item
        for item in list_requirements(db, tender.id)
        if item.kind == RequirementKind.PRODUCT.value
    ]

    manufacturer_names = {
        manufacturer.id: manufacturer.brand_name or manufacturer.legal_name
        for manufacturer in db.scalars(select(Manufacturer))
    }
    mirtek_ids = {
        manufacturer.id
        for manufacturer in db.scalars(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    }

    entries = [
        ComplianceEntryOut(
            id=entry.id,
            requirement_id=entry.requirement_id,
            manufacturer_id=entry.manufacturer_id,
            manufacturer_name=manufacturer_names.get(entry.manufacturer_id, "—"),
            status=entry.status,
            explanation=entry.explanation,
            source=entry.source,
            confidence=entry.confidence,
            needs_human_review=entry.needs_human_review,
        )
        for entry in db.scalars(
            select(ComplianceMatrixEntry).where(ComplianceMatrixEntry.tender_id == tender.id)
        )
    ]

    percentages = [
        WinPercentageOut(
            manufacturer_id=record.manufacturer_id,
            manufacturer_name=manufacturer_names.get(record.manufacturer_id, "—"),
            is_mirtek=record.manufacturer_id in mirtek_ids,
            percentage=record.percentage,
            reason_summary=record.reason_summary,
            requirements_total=record.requirements_total,
            requirements_scored=record.requirements_scored,
            calculated_at=record.calculated_at,
        )
        for record in db.scalars(
            select(WinPercentage).where(
                WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True)
            )
        )
    ]
    # МИРТЕК первым, дальше конкуренты по убыванию процента — так таблицу и читают.
    percentages.sort(key=lambda item: (not item.is_mirtek, -float(item.percentage)))

    return ComplianceMatrixOut(
        requirements=[RequirementOut.model_validate(requirement) for requirement in requirements],
        entries=entries,
        win_percentages=percentages,
    )


def attach_analysis_fields(
    db: Session, tenders: list[Tender], *, user_id: uuid.UUID | None = None
) -> list[dict]:
    """Дополняет тендеры полями, которых нет в самой таблице: AI-оценка по профилю, процент
    победителя МИРТЕК, число извлечённых требований и имя ответственного. По одному запросу
    на каждое поле для всей выдачи, а не по тендеру.

    AI-оценка (раздел 5.5.1 ТЗ) добавлена сюда, а не считается на лету в списке: с
    03.09.2026 её показывает каждая карточка списка цветным бейджем, и отдельный запрос на
    тендер дал бы N+1 на каждой странице."""

    if not tenders:
        return []

    ids = [tender.id for tender in tenders]

    # Избранное — личное, поэтому нужен пользователь; без него поле честно `False`.
    bookmarked: set[uuid.UUID] = set()
    if user_id is not None:
        from app.models.tender_bookmark import TenderBookmark

        bookmarked = set(
            db.scalars(
                select(TenderBookmark.tender_id).where(
                    TenderBookmark.user_id == user_id, TenderBookmark.tender_id.in_(ids)
                )
            )
        )

    # Теги — общие, поэтому пользователь не нужен; по одному запросу на всю выдачу.
    from app.models.tender_tag import TenderTag, TenderTagLink

    tags_by_tender: dict[uuid.UUID, list[TenderTag]] = defaultdict(list)
    for tender_id, tag in db.execute(
        select(TenderTagLink.tender_id, TenderTag)
        .join(TenderTag, TenderTag.id == TenderTagLink.tag_id)
        .where(TenderTagLink.tender_id.in_(ids))
        .order_by(func.lower(TenderTag.name))
    ).all():
        tags_by_tender[tender_id].append(tag)

    percentages: dict[uuid.UUID, Decimal] = dict(
        db.execute(
            select(WinPercentage.tender_id, WinPercentage.percentage)
            .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
            .where(
                WinPercentage.tender_id.in_(ids),
                Manufacturer.is_mirtek.is_(True),
                WinPercentage.is_current.is_(True),
            )
        ).all()
    )
    counts: dict[uuid.UUID, int] = dict(
        db.execute(
            select(Requirement.tender_id, func.count(Requirement.id))
            .where(Requirement.tender_id.in_(ids))
            .group_by(Requirement.tender_id)
        ).all()
    )
    scores: dict[uuid.UUID, tuple[Decimal | None, str | None, bool | None]] = {
        tender_id: (overall, verdict, decision)
        for tender_id, overall, verdict, decision in db.execute(
            select(
                AiProfileScore.tender_id,
                AiProfileScore.overall_score,
                AiProfileScore.verdict,
                AiProfileScore.decision,
            ).where(
                AiProfileScore.tender_id.in_(ids), AiProfileScore.is_current.is_(True)
            )
        ).all()
    }
    assignee_ids = [tender.assignee_id for tender in tenders if tender.assignee_id]
    assignees: dict[uuid.UUID, str] = (
        dict(
            db.execute(
                select(User.id, User.full_name).where(User.id.in_(assignee_ids))
            ).all()
        )
        if assignee_ids
        else {}
    )

    enriched = []
    for tender in tenders:
        data = {
            column.name: getattr(tender, column.name) for column in tender.__table__.columns
        }
        data["source"] = tender.source
        # Алиас поверх `stage`: колонки в БД нет, а внешний контракт поле по-прежнему
        # отдаёт (раздел 7 ТЗ), и `__table__.columns` его уже не даст.
        data["relevance_status"] = tender.relevance_status
        data["win_percentage"] = percentages.get(tender.id)
        overall, verdict, decision = scores.get(tender.id, (None, None, None))
        data["ai_score"] = overall
        data["ai_verdict"] = verdict
        data["ai_decision"] = decision
        data["requirements_count"] = counts.get(tender.id, 0)
        data["assignee_name"] = assignees.get(tender.assignee_id) if tender.assignee_id else None
        data["is_bookmarked"] = tender.id in bookmarked
        data["tags"] = tags_by_tender.get(tender.id, [])
        enriched.append(data)
    return enriched
