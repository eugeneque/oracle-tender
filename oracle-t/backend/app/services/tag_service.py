"""Теги закупок (замечание 17.09.2026): справочник тегов и набор тегов у закупки.
Смысл и границы — в докстринге `app/models/tender_tag.py`.

Смена набора тегов у закупки пишется в историю одной строкой («+срочно, −Россети»):
это факт работы с закупкой, как смена этапа или избранное, и по ленте карточки должно
быть видно, кто и когда её пометил."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_history import HistoryKind, TenderHistoryEntry
from app.models.tender_tag import DEFAULT_TAG_COLOR, TAG_COLORS, TenderTag, TenderTagLink
from app.models.user import User


class TagError(ValueError):
    """Ошибка входных данных — интерфейс показывает её текст как есть."""


def list_tags(db: Session) -> list[TenderTag]:
    return list(db.scalars(select(TenderTag).order_by(func.lower(TenderTag.name))))


def get_tag(db: Session, tag_id: uuid.UUID) -> TenderTag | None:
    return db.get(TenderTag, tag_id)


def _clean_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise TagError("Укажите название тега")
    return cleaned


def _clean_color(color: str | None) -> str:
    if not color:
        return DEFAULT_TAG_COLOR
    if color not in TAG_COLORS:
        raise TagError(f"Неизвестный цвет тега «{color}»")
    return color


def _name_taken(db: Session, name: str, *, except_id: uuid.UUID | None = None) -> bool:
    query = select(TenderTag.id).where(func.lower(TenderTag.name) == name.lower())
    if except_id is not None:
        query = query.where(TenderTag.id != except_id)
    return db.scalar(query) is not None


def create_tag(db: Session, *, name: str, color: str | None, actor: User) -> TenderTag:
    name = _clean_name(name)
    if _name_taken(db, name):
        raise TagError(f"Тег «{name}» уже есть")
    tag = TenderTag(name=name, color=_clean_color(color), created_by=actor.id)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def update_tag(
    db: Session, tag: TenderTag, *, name: str | None, color: str | None
) -> TenderTag:
    if name is not None:
        name = _clean_name(name)
        if _name_taken(db, name, except_id=tag.id):
            raise TagError(f"Тег «{name}» уже есть")
        tag.name = name
    if color is not None:
        tag.color = _clean_color(color)
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, tag: TenderTag) -> None:
    # Связи с закупками уходят каскадом по внешнему ключу (миграция 0045).
    db.delete(tag)
    db.commit()


def tags_of(db: Session, tender: Tender) -> list[TenderTag]:
    return list(
        db.scalars(
            select(TenderTag)
            .join(TenderTagLink, TenderTagLink.tag_id == TenderTag.id)
            .where(TenderTagLink.tender_id == tender.id)
            .order_by(func.lower(TenderTag.name))
        )
    )


def set_tags(
    db: Session, tender: Tender, tag_ids: list[uuid.UUID], *, actor: User
) -> list[TenderTag]:
    """Полный набор тегов закупки. Считает разницу с текущим и пишет её в историю."""

    wanted_ids = list(dict.fromkeys(tag_ids))
    wanted = {
        tag.id: tag
        for tag in db.scalars(select(TenderTag).where(TenderTag.id.in_(wanted_ids)))
    } if wanted_ids else {}
    missing = [str(tag_id) for tag_id in wanted_ids if tag_id not in wanted]
    if missing:
        raise TagError("Тег не найден — возможно, его только что удалили")

    current = {tag.id: tag for tag in tags_of(db, tender)}
    added = [wanted[tag_id] for tag_id in wanted_ids if tag_id not in current]
    removed = [tag for tag_id, tag in current.items() if tag_id not in wanted]

    for tag in added:
        db.add(TenderTagLink(tender_id=tender.id, tag_id=tag.id, created_by=actor.id))
    if removed:
        db.execute(
            TenderTagLink.__table__.delete().where(
                TenderTagLink.tender_id == tender.id,
                TenderTagLink.tag_id.in_([tag.id for tag in removed]),
            )
        )

    if added or removed:
        parts = [f"+{tag.name}" for tag in added] + [f"−{tag.name}" for tag in removed]
        db.add(
            TenderHistoryEntry(
                tender_id=tender.id,
                kind=HistoryKind.FIELD_CHANGE.value,
                field_name="tags",
                old_value=", ".join(tag.name for tag in current.values()) or None,
                new_value=", ".join(tag.name for tag in wanted.values()) or None,
                comment=", ".join(parts),
                user_id=actor.id,
            )
        )
    db.commit()
    return tags_of(db, tender)
