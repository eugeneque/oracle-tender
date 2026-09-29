"""Один разбор на все записи одной закупки (29.09.2026).

Одна закупка лежит в базе несколькими строками: из ЕИС, с площадки, где она проводится,
и отдельной строкой в канале Госплана — он собирается отдельно намеренно, чтобы каналы
можно было сравнивать (`tender_service._upsert_tender`). Разбор — документы, требования,
матрица, заключение ИИ — привязан к записи. Пользователь А разбирал запись из ЕИС,
пользователь Б открывал ту же закупку из Госплана и видел её неразобранной, а разбор,
запущенный там, шёл второй раз с нуля (32616398561 — три записи, три разбора за утро).

Теперь у записей с одним реестровым номером разбор один: он хранится у основной записи
группы, у остальных `analysis_tender_id` указывает на неё. Всё, что касается разбора —
эндпоинты карточки, постановка задач, поля списка, — идёт через `subject`.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai_profile import AiProfileScore
from app.models.analysis import Requirement
from app.models.tender import Tender

# Поля самой записи, которые заполняет разбор документации. Копируются на все записи
# закупки: по ним работают фильтры списка (тип конкурса, ОКПД2, регион, тип прибора), и
# запись из Госплана не должна выпадать из фильтра, раз её закупка уже разобрана.
SHARED_FIELDS = (
    "tender_type",
    "ai_comment",
    "okpd2_code",
    "region_organizer_code",
    "federal_district_code",
    "region_delivery_code",
    "meter_kinds",
)


def subject(db: Session, tender: Tender) -> Tender:
    """Запись, у которой хранится разбор этой закупки (для основной записи — она сама)."""

    if tender.analysis_tender_id is not None and tender.analysis_tender_id != tender.id:
        primary = db.get(Tender, tender.analysis_tender_id)
        if primary is not None:
            return primary
    return tender


def link(db: Session, tender: Tender) -> Tender:
    """Сводит записи закупки к одной основной и возвращает её.

    Основная — та, у которой уже есть разбор: свежая AI-оценка, затем больше требований,
    затем самая ранняя запись. Если группа уже сведена, ничего не меняется: разбор не
    переезжает с записи на запись из-за появления новой строки в Госплане."""

    if not tender.registry_number:
        return subject(db, tender)
    group = list(
        db.scalars(select(Tender).where(Tender.registry_number == tender.registry_number))
    )
    if len(group) <= 1:
        return tender

    ids = {item.id for item in group}
    primaries = {item.analysis_tender_id or item.id for item in group}
    if len(primaries) == 1 and next(iter(primaries)) in ids:
        return next(item for item in group if item.id in primaries)

    scored = dict(
        db.execute(
            select(AiProfileScore.tender_id, AiProfileScore.calculated_at).where(
                AiProfileScore.tender_id.in_(ids), AiProfileScore.is_current.is_(True)
            )
        ).all()
    )
    requirements = dict(
        db.execute(
            select(Requirement.tender_id, func.count(Requirement.id))
            .where(Requirement.tender_id.in_(ids))
            .group_by(Requirement.tender_id)
        ).all()
    )

    def rank(item: Tender) -> tuple:
        calculated = scored.get(item.id)
        return (
            calculated is None,
            -(calculated.timestamp() if calculated else 0),
            -requirements.get(item.id, 0),
            item.created_at,
            str(item.id),
        )

    primary = min(group, key=rank)
    for item in group:
        item.analysis_tender_id = None if item.id == primary.id else primary.id
    db.flush()
    return primary


def twins(db: Session, primary: Tender) -> list[Tender]:
    """Остальные записи закупки, чей разбор хранится у `primary`."""

    return list(db.scalars(select(Tender).where(Tender.analysis_tender_id == primary.id)))


def share_fields(db: Session, primary: Tender) -> None:
    """Переносит на записи-двойники поля, заполненные разбором (`SHARED_FIELDS`). Пустые
    значения основной записи не переносятся — они не стирают то, что двойник знает сам
    (например, регион из API Госплана)."""

    for twin in twins(db, primary):
        for field in SHARED_FIELDS:
            value = getattr(primary, field)
            if value not in (None, [], "") and getattr(twin, field) != value:
                setattr(twin, field, value)
    db.flush()


def subject_ids(db: Session, tender_ids: list) -> dict:
    """Для выдачи списка: id записи → id записи, у которой хранится её разбор."""

    if not tender_ids:
        return {}
    rows = db.execute(
        select(Tender.id, Tender.analysis_tender_id).where(Tender.id.in_(tender_ids))
    ).all()
    return {row.id: row.analysis_tender_id or row.id for row in rows}
