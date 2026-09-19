"""Правки тендера человеком и история изменений (раздел 5.6 ТЗ — Этап 7).

ИИ ошибается в классификации: путает регион поставки с регионом заказчика, ставит «прочее»
там, где есть работы, промахивается по ОКПД2. Поэтому карточка обязана быть редактируемой,
а каждая правка — прослеживаемой: кто, что и когда поправил.

Каждое изменение попадает в два места: в `tender_history` (лента карточки, пара
«было → стало») и в общий журнал `logs` (раздел 5.9 ТЗ). Дублирование намеренное — журнал
отвечает на вопрос «что вообще происходило в системе», история — «почему в этом тендере
стоит такой тип конкурса».
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.region import FederalDistrict, Region
from app.models.tender import RelevanceStatus, Tender, TenderStage, TenderStatus, TenderType
from app.models.tender_history import HistoryKind, TenderHistoryEntry
from app.models.user import User
from app.services.audit import log_action


class TenderEditError(ValueError):
    """Недопустимое значение в правке — эндпоинт превращает это в 422 с текстом ошибки."""


# Поля, которые человек может исправлять в карточке (раздел 5.6 ТЗ, «исправить
# классификацию»). Всё остальное приходит от источника или ИИ и правится пересбором/
# переанализом, а не руками.
EDITABLE_FIELDS = (
    "tender_type",
    "region_organizer_code",
    "region_delivery_code",
    "federal_district_code",
    "okpd2_code",
    "relevance_status",
    "stage",
    "assignee_id",
    "ai_comment",
)

FIELD_LABELS: dict[str, str] = {
    "tender_type": "Тип конкурса",
    "region_organizer_code": "Регион заказчика",
    "region_delivery_code": "Регион поставки",
    "federal_district_code": "Федеральный округ",
    "okpd2_code": "Код ОКПД2",
    "relevance_status": "Релевантность",
    "stage": "Этап работы",
    "assignee_id": "Ответственный за тендер",
    "ai_comment": "Комментарий ИИ",
    "bookmark": "Избранное",
    "tags": "Теги",
}


def _validate(db: Session, field_name: str, value) -> None:
    """Проверка значения по справочникам. Пустое значение (None) разрешено всегда: это
    «снять классификацию», нормальное действие, когда ИИ поставил заведомую чушь."""

    if value is None:
        return

    if field_name == "tender_type":
        allowed = {item.value for item in TenderType}
        if value not in allowed:
            raise TenderEditError(f"Недопустимый тип конкурса: {value}")
    elif field_name == "relevance_status":
        allowed = {item.value for item in RelevanceStatus}
        if value not in allowed:
            raise TenderEditError(f"Недопустимый статус релевантности: {value}")
    elif field_name == "stage":
        allowed = {item.value for item in TenderStage}
        if value not in allowed:
            raise TenderEditError(f"Недопустимый этап работы: {value}")
    elif field_name == "assignee_id":
        if db.get(User, value) is None:
            raise TenderEditError("Указанный ответственный не найден среди пользователей")
    elif field_name in ("region_organizer_code", "region_delivery_code"):
        if db.get(Region, value) is None:
            raise TenderEditError(f"Неизвестный код региона: {value}")
    elif field_name == "federal_district_code":
        if db.get(FederalDistrict, value) is None:
            raise TenderEditError(f"Неизвестный код федерального округа: {value}")


def _as_text(value) -> str | None:
    return None if value is None else str(value)


def record_change(
    db: Session,
    tender: Tender,
    *,
    field_name: str,
    old_value,
    new_value,
    actor: User | None,
) -> TenderHistoryEntry:
    entry = TenderHistoryEntry(
        tender_id=tender.id,
        user_id=actor.id if actor else None,
        kind=HistoryKind.FIELD_CHANGE.value,
        field_name=field_name,
        old_value=_as_text(old_value),
        new_value=_as_text(new_value),
    )
    db.add(entry)
    db.flush()
    return entry


def update_tender(
    db: Session, tender: Tender, changes: dict, *, actor: User
) -> list[TenderHistoryEntry]:
    """Применяет правки и возвращает записи истории по фактически изменённым полям.

    Поля, значение которых совпадает с текущим, пропускаются молча — иначе повторное
    сохранение формы засоряло бы историю пустыми записями «было X, стало X».
    """

    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise TenderEditError(f"Недопустимые поля: {', '.join(sorted(unknown))}")

    for field_name, value in changes.items():
        _validate(db, field_name, value)

    entries: list[TenderHistoryEntry] = []
    for field_name, value in changes.items():
        old_value = getattr(tender, field_name)
        if old_value == value:
            continue
        setattr(tender, field_name, value)
        entries.append(
            record_change(
                db, tender, field_name=field_name, old_value=old_value, new_value=value, actor=actor
            )
        )

    # Федеральный округ определяется регионом заказчика (Приложение G ТЗ): если человек
    # поправил регион, а округ не трогал, оставлять прежний округ нельзя — карточка стала
    # бы противоречить сама себе.
    if "region_organizer_code" in changes and "federal_district_code" not in changes:
        region = (
            db.get(Region, changes["region_organizer_code"])
            if changes["region_organizer_code"]
            else None
        )
        district_code = region.federal_district_code if region else None
        if tender.federal_district_code != district_code:
            old_value = tender.federal_district_code
            tender.federal_district_code = district_code
            entries.append(
                record_change(
                    db,
                    tender,
                    field_name="federal_district_code",
                    old_value=old_value,
                    new_value=district_code,
                    actor=actor,
                )
            )

    if entries:
        details = "; ".join(
            f"{FIELD_LABELS.get(entry.field_name, entry.field_name)}: "
            f"{entry.old_value or '—'} → {entry.new_value or '—'}"
            for entry in entries
        )
        log_action(
            db,
            component="tenders",
            action=f"update_tender:{tender.external_id}",
            result="success",
            level=LogLevel.INFO,
            details=details,
            user_id=actor.id,
        )

    db.commit()
    db.refresh(tender)
    return entries


def add_comment(db: Session, tender: Tender, text: str, *, actor: User) -> TenderHistoryEntry:
    """Комментарий пользователя к тендеру (раздел 5.6 ТЗ). Попадает в ту же ленту, что и
    правки, — обсуждение и решения читаются вместе."""

    comment = text.strip()
    if not comment:
        raise TenderEditError("Комментарий не может быть пустым")

    entry = TenderHistoryEntry(
        tender_id=tender.id,
        user_id=actor.id,
        kind=HistoryKind.COMMENT.value,
        comment=comment,
    )
    db.add(entry)
    log_action(
        db,
        component="tenders",
        action=f"comment_tender:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=comment[:500],
        user_id=actor.id,
    )
    db.commit()
    db.refresh(entry)
    return entry


def list_history(db: Session, tender_id: uuid.UUID) -> list[tuple[TenderHistoryEntry, str | None]]:
    """Лента истории с именами авторов. Имя подставляется здесь, а не подгружается фронтом
    вторым запросом: без него запись «кто поправил» бессмысленна."""

    rows = db.execute(
        select(TenderHistoryEntry, User.full_name)
        .join(User, User.id == TenderHistoryEntry.user_id, isouter=True)
        .where(TenderHistoryEntry.tender_id == tender_id)
        .order_by(TenderHistoryEntry.created_at.desc())
    ).all()
    return [(entry, full_name) for entry, full_name in rows]


def set_relevance(
    db: Session, tender: Tender, relevance_status: str, *, actor: User
) -> Tender:
    """Подтверждение релевантности / отметка «неактуально» (раздел 5.6 ТЗ).

    Тонкая обёртка над `update_tender`, чтобы у отдельного эндпоинта релевантности была
    та же валидация и та же запись в историю, что и у общей правки карточки."""

    allowed = {item.value for item in RelevanceStatus}
    if relevance_status not in allowed:
        raise TenderEditError(f"Недопустимый статус релевантности: {relevance_status}")
    update_tender(db, tender, {"relevance_status": relevance_status}, actor=actor)
    return tender


def set_stage(db: Session, tender: Tender, stage: str, *, actor: User) -> Tender:
    """Перевод тендера на другой этап пайплайна (раздел 5.6 ТЗ).

    Отдельная обёртка, как и `set_relevance`: этап меняют перетаскиванием карточки на доске,
    а это должно попадать в историю так же, как правка из формы."""

    allowed = {item.value for item in TenderStage}
    if stage not in allowed:
        raise TenderEditError(f"Недопустимый этап работы: {stage}")
    update_tender(db, tender, {"stage": stage}, actor=actor)
    return tender


def known_statuses() -> list[str]:
    return [item.value for item in TenderStatus]


def known_stages() -> list[str]:
    return [item.value for item in TenderStage]
