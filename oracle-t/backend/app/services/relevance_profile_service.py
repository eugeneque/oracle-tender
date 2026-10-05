"""Профили отбора: совпадения с закупками, предпросмотр, доступ.

Сопоставление — общий движок `relevance_service.match_profile` (одна логика для общих и
личных профилей):

* встретилось исключение — закупка не подходит, что бы ни совпало;
* заданы ключевые слова — нужно совпадение (любое или все, по `match_mode`);
* заданы коды ОКПД2 — код закупки начинается с одного из них;
* слова и коды сочетаются по `okpd2_mode`: `narrow` — нужно и то и другое, `either` —
  достаточно одного.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models.relevance_profile import RelevanceProfile, RelevanceProfileMatch
from app.models.tender import Tender
from app.models.user import User, UserRole
from app.services import relevance_service
from app.services.relevance_service import match_profile, tender_text, tokenize

BATCH = 2000


def has_rules(profile: RelevanceProfile) -> bool:
    return bool(profile.keywords or profile.okpd2_codes)


def profile_matches(profile, tender) -> bool:
    """Подходит ли закупка профилю. `tender` — Tender или строка с теми же полями."""

    return match_profile(tokenize(tender_text(tender)), profile, tender.okpd2_code).matched


PREVIEW_SAMPLES = 8


def preview(db: Session, profile) -> dict:
    """Что отберёт профиль, ещё не сохранённый: число закупок и примеры, свежие первыми.

    Считается в памяти по всем закупкам — около полусекунды на базе в 20 тысяч. Нужен,
    чтобы термин добавляли, видя результат, а не наугад: «счетчик*» без исключений тянет
    счётчики монет и клеток, и это видно в примерах сразу.
    """

    from sqlalchemy import func

    from app.models.source import Source

    source_ids = None
    if profile.source_keys:
        source_ids = set(db.scalars(select(Source.id).where(Source.key.in_(profile.source_keys))))
    query = select(
        Tender.id,
        Tender.title,
        Tender.customer_name,
        Tender.procurement_method,
        Tender.okpd2_code,
        Tender.source_id,
        Tender.publish_date,
    ).order_by(Tender.publish_date.desc().nullslast())
    count = 0
    samples: list[dict] = []
    for row in db.execute(query.execution_options(yield_per=BATCH)):
        if source_ids is not None and row.source_id not in source_ids:
            continue
        if not profile_matches(profile, row):
            continue
        count += 1
        if len(samples) < PREVIEW_SAMPLES:
            samples.append(
                {
                    "id": str(row.id),
                    "title": row.title,
                    "okpd2_code": row.okpd2_code,
                    "publish_date": row.publish_date.isoformat() if row.publish_date else None,
                }
            )
    total = db.scalar(select(func.count()).select_from(Tender)) or 0
    return {"count": count, "total": total, "samples": samples}


def after_default_change(db: Session) -> None:
    """Общие профили изменились — отметка `passed_relevance_filter` пересчитывается сразу.

    Раньше после правки групп нужно было нажать «Пересчитать», и до этого список показывал
    отметки от прежних настроек. Проход по базе — пара секунд, поэтому делается сам.
    """

    relevance_service.backfill(db)


def _candidate_rows(db: Session, since: datetime | None):
    query = select(
        Tender.id,
        Tender.title,
        Tender.customer_name,
        Tender.procurement_method,
        Tender.okpd2_code,
    )
    if since is not None:
        query = query.where(Tender.updated_at >= since)
    return db.execute(query.execution_options(yield_per=BATCH))


def refresh_matches(db: Session, profile: RelevanceProfile, *, force: bool = False) -> int:
    """Приводит совпадения профиля в актуальное состояние, возвращает их число.

    Правила изменились — пересчёт по всем закупкам; иначе только по тем, что изменились с
    прошлого пересчёта (новые из сбора, поправленные вручную, получившие код ОКПД2 после
    анализа). Начало отсчёта берётся ДО выборки, чтобы закупка, обновлённая во время
    пересчёта, попала в следующий, а не потерялась между ними.
    """

    full = force or profile.matched_at is None or profile.matched_version != profile.rules_version
    started_at = datetime.now(timezone.utc)
    since = None if full else profile.matched_at

    matched_ids: list[uuid.UUID] = []
    seen_ids: list[uuid.UUID] = []
    for row in _candidate_rows(db, since):
        seen_ids.append(row.id)
        if profile_matches(profile, row):
            matched_ids.append(row.id)

    if full:
        db.execute(
            delete(RelevanceProfileMatch).where(RelevanceProfileMatch.profile_id == profile.id)
        )
    else:
        for start in range(0, len(seen_ids), BATCH):
            db.execute(
                delete(RelevanceProfileMatch).where(
                    RelevanceProfileMatch.profile_id == profile.id,
                    RelevanceProfileMatch.tender_id.in_(seen_ids[start : start + BATCH]),
                )
            )
    for start in range(0, len(matched_ids), BATCH):
        db.execute(
            insert(RelevanceProfileMatch),
            [
                {"profile_id": profile.id, "tender_id": tender_id}
                for tender_id in matched_ids[start : start + BATCH]
            ],
        )

    profile.matched_version = profile.rules_version
    profile.matched_at = started_at
    db.flush()
    return count_matches(db, profile.id)


def count_matches(db: Session, profile_id: uuid.UUID) -> int:
    from sqlalchemy import func

    return (
        db.scalar(
            select(func.count()).where(RelevanceProfileMatch.profile_id == profile_id)
        )
        or 0
    )


def prepare_for_filter(db: Session, profile_id: uuid.UUID) -> RelevanceProfile | None:
    """Профиль для фильтра списка: досчитывает совпадения и фиксирует их в базе.

    `None` — профиля нет (удалён, пока у кого-то открыта страница): вызывающий решает, как
    отвечать; молча отдать «ничего не подошло» значило бы показать пустой список вместо
    объяснения.
    """

    # Блокировка строки профиля: список, доска и счётчики открываются одновременно с одним и
    # тем же профилем, и без неё два пересчёта вставляли бы одни и те же совпадения —
    # IntegrityError и 500 у пользователя. Второй запрос ждёт первого и после него
    # досчитывает уже почти ничего.
    profile = db.scalar(
        select(RelevanceProfile)
        .where(RelevanceProfile.id == profile_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None:
        return None
    refresh_matches(db, profile)
    db.commit()
    return profile


def can_edit(profile: RelevanceProfile, user: User) -> bool:
    """Общие профили — только администратор; личные — автор или администратор."""

    if user.role == UserRole.ADMIN.value:
        return True
    return not profile.is_default and profile.owner_id == user.id
