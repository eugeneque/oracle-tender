"""Избранные закупки: добавить, убрать, прочитать заметку (замечание тестировщика
16.09.2026). Смысл и границы — в докстринге `app/models/tender_bookmark.py`.

Действие пишется в историю тендера: кто и когда отложил закупку — факт работы с ней, а
история карточки и есть журнал такой работы."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_bookmark import TenderBookmark
from app.models.tender_history import HistoryKind, TenderHistoryEntry
from app.models.user import User


def get(db: Session, tender: Tender, user: User) -> TenderBookmark | None:
    return db.scalar(
        select(TenderBookmark).where(
            TenderBookmark.tender_id == tender.id, TenderBookmark.user_id == user.id
        )
    )


def add(db: Session, tender: Tender, user: User, *, note: str | None = None) -> TenderBookmark:
    bookmark = get(db, tender, user)
    created = bookmark is None
    if bookmark is None:
        bookmark = TenderBookmark(tender_id=tender.id, user_id=user.id)
        db.add(bookmark)
    bookmark.note = (note or "").strip() or None
    if created:
        db.add(
            TenderHistoryEntry(
                tender_id=tender.id,
                kind=HistoryKind.FIELD_CHANGE.value,
                field_name="bookmark",
                new_value="в избранном",
                comment=bookmark.note,
                user_id=user.id,
            )
        )
    db.commit()
    db.refresh(bookmark)
    return bookmark


def remove(db: Session, tender: Tender, user: User) -> bool:
    bookmark = get(db, tender, user)
    if bookmark is None:
        return False
    db.delete(bookmark)
    db.add(
        TenderHistoryEntry(
            tender_id=tender.id,
            kind=HistoryKind.FIELD_CHANGE.value,
            field_name="bookmark",
            new_value="убрано из избранного",
            user_id=user.id,
        )
    )
    db.commit()
    return True
