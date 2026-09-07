"""История участий МИРТЕК (раздел 5.6 «Настройки → Моя компания», раздел 7 ТЗ).

Основной источник измерения History (раздел 5.5.1): по этим записям система отвечает на
вопрос «выигрывали ли мы такие тендеры раньше». Отсюда два способа наполнения — выгрузка из
реестра контрактов ЕИС по ИНН компании и ручное добавление того, что выгрузка не покрыла
(в реестре контрактов есть только победы, проигрыши вносятся руками).

Читать может любой пользователь: карточка тендера объясняет History ссылками на конкретные
участия, и без доступа к ним объяснение было бы нечитаемым. Менять и синхронизировать —
только администратор: это вход главной метрики по всем тендерам сразу.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.company_participation import (
    OUTCOME_LABELS,
    SOURCE_LABELS,
    ParticipationOutcome,
)
from app.models.user import User
from app.schemas.company_participation import (
    ParticipationListOut,
    ParticipationOut,
    ParticipationSummaryOut,
    ParticipationSyncResultOut,
    ParticipationWrite,
)
from app.services import company_participation_service
from app.services.company_participation_service import ParticipationError

router = APIRouter(prefix="/company-participations", tags=["company-participations"])


def _out(record) -> ParticipationOut:
    return ParticipationOut(
        id=record.id,
        tender_id=record.tender_id,
        external_tender_id=record.external_tender_id,
        tender_title=record.tender_title,
        customer_name=record.customer_name,
        customer_org_id=record.customer_org_id,
        our_inn=record.our_inn,
        our_bid=record.our_bid,
        price_drop_pct=record.price_drop_pct,
        competitors_count=record.competitors_count,
        outcome=record.outcome,
        # Подпись проставляется здесь, а не на фронте: перечень исходов и их русские
        # названия должны жить в одном месте (тот же приём, что у вердикта AI-оценки).
        outcome_label=OUTCOME_LABELS.get(record.outcome, record.outcome),
        final_contract_value=record.final_contract_value,
        executed_at=record.executed_at,
        lessons_learned_md=record.lessons_learned_md,
        source=record.source,
        source_label=SOURCE_LABELS.get(record.source, record.source),
        last_synced_at=record.last_synced_at,
        created_at=record.created_at,
    )


def _summary_out(db: Session) -> ParticipationSummaryOut:
    summary = company_participation_service.summary(db)
    return ParticipationSummaryOut(
        total=summary.total,
        won=summary.won,
        lost=summary.lost,
        disqualified=summary.disqualified,
        unknown=summary.unknown,
        win_rate=summary.win_rate,
        wins_only_data=company_participation_service.has_only_wins_source(db),
        last_synced_at=summary.last_synced_at,
    )


def _validate_outcome(outcome: str) -> None:
    if outcome not in {item.value for item in ParticipationOutcome}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неизвестный исход «{outcome}»",
        )


@router.get("", response_model=ParticipationListOut)
def list_participations(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ParticipationListOut:
    """Список участий и сводка. Сводка считается по всей истории, а не по странице —
    иначе win-rate менялся бы при листании."""

    items = company_participation_service.list_participations(db, limit=limit, offset=offset)
    return ParticipationListOut(
        items=[_out(item) for item in items], summary=_summary_out(db)
    )


@router.post("", response_model=ParticipationOut, status_code=status.HTTP_201_CREATED)
def create_participation(
    payload: ParticipationWrite,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ParticipationOut:
    """Ручное добавление участия, не покрытого синхронизацией (раздел 5.6 ТЗ)."""

    _validate_outcome(payload.outcome)
    try:
        record = company_participation_service.create_participation(
            db, payload.model_dump(exclude_unset=True), actor=admin
        )
    except ParticipationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _out(record)


@router.put("/{participation_id}", response_model=ParticipationOut)
def update_participation(
    participation_id: uuid.UUID,
    payload: ParticipationWrite,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ParticipationOut:
    _validate_outcome(payload.outcome)
    try:
        record = company_participation_service.update_participation(
            db, participation_id, payload.model_dump(exclude_unset=True), actor=admin
        )
    except ParticipationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _out(record)


@router.delete("/{participation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_participation(
    participation_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    try:
        company_participation_service.delete_participation(db, participation_id, actor=admin)
    except ParticipationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sync", response_model=ParticipationSyncResultOut)
def sync_participations(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ParticipationSyncResultOut:
    """Обновление истории участий из ЕИС — победы и проигрыши двумя разными путями.

    Победы берутся из реестра контрактов по ИНН. Проигрыши там отсутствуют по устройству
    (в реестре только заключённые контракты), а отдельного источника у них нет: реестра
    протоколов с поиском по участнику в ЕИС не существует, а в самих протоколах участники
    обезличены — раскрывается только победитель. Поэтому второй шаг выводит их из
    собственного пайплайна: закупка с отметкой «заявка подана» завершилась, контракт достался
    не нам — проигрыш.

    Выполняется синхронно: это несколько страниц поисковой выдачи плюс по одному запросу на
    закупку с поданной заявкой, и результат человек ждёт на экране.
    """

    try:
        result = company_participation_service.sync_from_eis(db, actor=admin)
    except ParticipationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return ParticipationSyncResultOut(
        fetched=result.fetched,
        created=result.created,
        updated=result.updated,
        matched_tenders=result.matched_tenders,
        checked_submitted=result.checked_submitted,
        losses_found=result.losses_found,
    )
