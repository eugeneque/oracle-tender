"""Автоматический разбор закупок через YandexGPT (30.10.2026, раздел 81 ARCHITECTURE.md).

Яндекс временно отдаёт свои модели бесплатно. Пока так, ждать, что человек откроет карточку и
нажмёт «Разбор ИИ», незачем: открытые релевантные закупки без заключения сами встают на
полный разбор (анализ документов → матрица → AI-оценка) — по одной, ближайший срок подачи
первым.

Почему по одной и через планировщик, а не всё сразу при включении: очередь фоновых задач
даёт «системе» один поток (`jobs.dispatch` — не больше одной задачи на владельца), и сотня
задач авторазбора встала бы перед ночной достройкой матриц и ИИ-отбором. Планировщик раз в
минуту ставит следующую закупку, только когда предыдущая закончилась.

Модель закреплена за задачей (`payload["provider"]`, см. `ai_context.forcing_provider`):
ни личный выбор, ни системная DeepSeek, ни подмена выключенной модели авторазбор не
касаются. Выключили Yandex — очередь стоит, платные токены не тратятся.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.jobs import enqueue
from app.models.ai_profile import AiProfileScore
from app.models.integration_setting import AiProviderSettings
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.log import LogLevel
from app.models.tender import Tender, TenderStage
from app.models.user import User
from app.services import ai_provider_service
from app.services.ai_provider_service import PROVIDER_YANDEX
from app.services.audit import log_action

AUTO_PROVIDER = PROVIDER_YANDEX
_ACTIVE = [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
# Этапы, на которых закупку ещё есть смысл разбирать: отклонённые, проигранные и выигранные
# заключение ИИ уже не изменит.
_OPEN_STAGES = [TenderStage.AI_SELECTED.value, TenderStage.UNDER_REVIEW.value]


def _settings(db: Session) -> AiProviderSettings:
    return ai_provider_service._get_or_create(db)  # noqa: SLF001 - синглтон настроек моделей


def is_enabled(db: Session) -> bool:
    # Отдельным SELECT, как `get_disabled_providers`: планировщик держит сессию недолго, но
    # закэшированный объект не должен скрыть выключение, сделанное минуту назад.
    return bool(
        db.execute(
            select(AiProviderSettings.auto_review_enabled)
        ).scalar_one_or_none()
    )


def _is_auto_job():
    return BackgroundJob.payload.op("->>")("auto") == "true"


def blocker(db: Session) -> str | None:
    """Почему очередь сейчас стоит — тем текстом, что увидит администратор; `None` — идёт."""

    if not ai_provider_service.is_provider_configured(db, AUTO_PROVIDER):
        return "YandexGPT не настроена: заполните Folder ID и API-ключ"
    if not ai_provider_service.is_provider_enabled(db, AUTO_PROVIDER):
        return "YandexGPT отключена администратором"
    return None


def _candidates(db: Session):
    """Закупки, ждущие авторазбора: `(id основной записи, ближайший срок подачи)`.

    Отбор — тот же, что у списка по умолчанию: прошли общие профили, модель не отклонила,
    этап открытый, приём заявок не закончился. Разбор хранится у основной записи закупки
    (`tender_twins`), поэтому группировка — по ней: три записи одной закупки — один разбор.
    Пропускаются закупки, у которых уже есть заключение ИИ (разобраны вручную), которые
    разбираются прямо сейчас, и те, что авторазбор уже брал, — неудачная попытка не
    повторяется по кругу каждую минуту; повторить можно кнопкой в карточке."""

    subject_id = func.coalesce(Tender.analysis_tender_id, Tender.id)
    scored = select(AiProfileScore.tender_id)
    taken = select(BackgroundJob.tender_id).where(
        BackgroundJob.tender_id.is_not(None), _is_auto_job()
    )
    busy = select(BackgroundJob.tender_id).where(
        BackgroundJob.tender_id.is_not(None), BackgroundJob.status.in_(_ACTIVE)
    )
    deadline = func.min(Tender.application_end).label("deadline")
    return (
        select(subject_id.label("subject_id"), deadline)
        .where(
            Tender.passed_relevance_filter.is_(True),
            Tender.ai_relevant.is_not(False),
            Tender.stage.in_(_OPEN_STAGES),
            Tender.application_end > datetime.now(timezone.utc),
            subject_id.not_in(scored),
            subject_id.not_in(taken),
            subject_id.not_in(busy),
        )
        .group_by(subject_id)
        .order_by(deadline.asc(), subject_id)
    )


def _current(db: Session) -> BackgroundJob | None:
    return db.execute(
        select(BackgroundJob)
        .where(_is_auto_job(), BackgroundJob.status.in_(_ACTIVE))
        .order_by(BackgroundJob.created_at)
        .limit(1)
    ).scalar_one_or_none()


def feed(db: Session) -> BackgroundJob | None:
    """Ставит следующую закупку на разбор, если авторазбор включён, Yandex доступна и
    предыдущая задача авторазбора закончилась. Возвращает поставленную задачу."""

    if not is_enabled(db) or blocker(db) is not None or _current(db) is not None:
        return None
    row = db.execute(_candidates(db).limit(1)).first()
    if row is None:
        return None
    tender = db.get(Tender, row.subject_id)
    if tender is None:
        return None
    return enqueue(
        db,
        kind=JobKind.TENDER_FULL_REVIEW,
        tender=tender,
        actor=None,
        payload={"auto": True, "provider": AUTO_PROVIDER},
    )


def status(db: Session) -> dict:
    counts = dict(
        db.execute(
            select(BackgroundJob.status, func.count())
            .where(_is_auto_job())
            .group_by(BackgroundJob.status)
        ).all()
    )
    pending = db.scalar(select(func.count()).select_from(_candidates(db).subquery())) or 0
    current = _current(db)
    current_tender = db.get(Tender, current.tender_id) if current and current.tender_id else None
    return {
        "enabled": is_enabled(db),
        "provider": AUTO_PROVIDER,
        "blocker": blocker(db),
        "pending": pending,
        "done": counts.get(JobStatus.SUCCESS.value, 0),
        "failed": counts.get(JobStatus.ERROR.value, 0) + counts.get(JobStatus.CANCELLED.value, 0),
        "current": (
            {
                "job_id": current.id,
                "status": current.status,
                "message": current.message,
                "tender_id": current_tender.id,
                "external_id": current_tender.external_id,
                "title": current_tender.title,
            }
            if current and current_tender
            else None
        ),
    }


def set_enabled(db: Session, enabled: bool, *, actor: User) -> dict:
    """Включает или выключает авторазбор. Уже идущая задача при выключении доделывается —
    это одна закупка, а оборванный на середине разбор оставил бы матрицу с дырой."""

    settings = _settings(db)
    settings.auto_review_enabled = enabled
    settings.updated_by_id = actor.id
    log_action(
        db,
        component="ai_models",
        action="auto_review:on" if enabled else "auto_review:off",
        result="success",
        level=LogLevel.INFO,
        details=(
            "Автоматический разбор через YandexGPT включён"
            if enabled
            else "Автоматический разбор выключен"
        ),
        user_id=actor.id,
    )
    db.commit()
    if enabled:
        # Первая закупка — сразу, а не через минуту: администратор видит, что очередь пошла.
        feed(db)
    return status(db)
