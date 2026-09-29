"""Обработчики фоновых задач: что именно выполняется для каждого вида задачи из
`app/core/jobs.py`. Вынесено из модуля очереди, чтобы очередь не зависела от прикладных
сервисов (иначе `jobs` → `tender_analysis` → `jobs` замкнулись бы в цикл импортов).

Импортируется один раз при старте приложения (`app/main.py`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.jobs import (
    current_job_created_at,
    enqueue,
    raise_if_cancelled,
    register_handler,
    register_job_handler,
    report_progress,
)
from app.models.analysis import Requirement, RequirementKind, WinPercentage
from app.models.ai_profile import AiProfileScore
from app.models.job import BackgroundJob, JobKind, JobStatus
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender
from app.models.user import User
from app.services import (
    ai_feedback_service,
    notification_service,
    tender_card_service,
    tender_insights,
    tender_twins,
)
from app.services.ai_profile_service import compute_profile_score
from app.services.ai_provider_service import PROVIDER_LABELS, get_active_provider
from app.services.compliance_service import evaluate_tender
from app.services.document_service import sync_tender_documents_with_status
from app.services.similarity_service import refresh_similar
from app.services.tender_analysis import analyze_tender, kinds_summary
from app.services.tender_service import (
    POLL_EXCLUDED_SOURCE_TYPES,
    check_pending_ai_relevance,
    get_source_by_key,
    poll_source,
)


def _run_analysis(db: Session, tender: Tender, actor: User | None) -> str:
    # Документы должны быть скачаны и распознаны — иначе анализировать нечего, кроме
    # наименования (раздел 5.2 ТЗ). Раньше это делал HTTP-эндпоинт до вызова анализа.
    _, documents_error = sync_tender_documents_with_status(db, tender)
    outcome = analyze_tender(db, tender, actor=actor)

    parts = [f"требований сохранено: {outcome.requirements_saved}{kinds_summary(outcome)}"]
    # Несостоявшаяся загрузка документов называется первой и прямо: «требований сохранено: 0»
    # после сброшенного соединения с ЕИС — не итог анализа, а его отсутствие, и человек
    # должен понять, что нужно восстановить доступ к площадке и запустить анализ ещё раз, а
    # не искать в закупке ТЗ, которого система не видела.
    if documents_error:
        parts.append(
            f"документы с площадки не получены ({documents_error}) — анализ выполнен только "
            "по наименованию; восстановите доступ к площадке и запустите анализ ещё раз"
        )
    if outcome.requirements_skipped:
        parts.append(f"пропущено: {outcome.requirements_skipped}")
    if outcome.chunks_failed:
        parts.append(f"неразобранных фрагментов: {outcome.chunks_failed}")
    if outcome.tender_type:
        parts.append(f"тип: {outcome.tender_type}")
    if outcome.okpd2_code:
        parts.append(f"ОКПД2: {outcome.okpd2_code}")
    parts.extend(outcome.messages)

    # Вектор и список похожих обновляются здесь же: требования только что извлечены, а
    # именно они делают «похожесть» осмысленной (без них похожими оказываются все закупки
    # с похожими названиями). Сбой не отменяет анализ — вкладка «Похожие» просто останется
    # с прежним содержимым.
    similarity = refresh_similar(db, tender)
    if similarity.pairs_saved:
        parts.append(f"похожих тендеров: {similarity.pairs_saved}")
    parts.extend(similarity.messages)

    notification_service.notify_new_relevant_tender(db, tender)
    return "; ".join(parts)


# «Достроить, а не пересчитать»: пропускаются производители, чей итог новее требований.
_GAP_ONLY = datetime.min.replace(tzinfo=timezone.utc)


def _run_evaluation(
    db: Session, tender: Tender, actor: User | None, *, since: datetime | None = None
) -> str:
    outcome = evaluate_tender(db, tender, actor=actor, skip_fresh_since=since)

    parts = [
        f"производителей обработано: {outcome.manufacturers_processed}",
        f"ячеек матрицы: {outcome.entries_saved}",
    ]
    if outcome.needs_review:
        parts.append(f"требуют проверки: {outcome.needs_review}")
    if outcome.manual_fallback_used:
        parts.append(f"через руководство пользователя: {outcome.manual_fallback_used}")
    parts.extend(outcome.messages)

    return "; ".join(parts)



def matrix_gap(db: Session, tender: Tender) -> str | None:
    """Почему матрицу соответствия нужно (пере)строить — или `None`, если она актуальна.

    Бреши, которые закрывает (28.09.2026): матрицы нет вовсе, хотя требования к товару
    извлечены (полный разбор оборвался на втором шаге); в ней не все производители
    (справочник пополнился после расчёта); требования переизвлечены после расчёта — ячейки
    матрицы ссылаются на прежний список. Закупка без требований к товару матрицы не требует.
    """

    product = db.execute(
        select(func.count(), func.max(Requirement.created_at)).where(
            Requirement.tender_id == tender.id,
            Requirement.kind == RequirementKind.PRODUCT.value,
        )
    ).one()
    if not product[0]:
        return None
    wins = db.execute(
        select(func.count(), func.min(WinPercentage.calculated_at)).where(
            WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True)
        )
    ).one()
    if not wins[0]:
        return "матрица соответствия не построена"
    manufacturers = db.scalar(select(func.count()).select_from(Manufacturer)) or 0
    if wins[0] < manufacturers:
        return f"в матрице {wins[0]} производителей из {manufacturers}"
    if product[1] is not None and wins[1] is not None and wins[1] < product[1]:
        return "требования обновлены после расчёта матрицы"
    return None


def prepare_tender(db: Session, tender: Tender, actor: User | None) -> list[str]:
    """Достраивает то, без чего заключение ИИ выходит с дырой: анализ документации (если
    требований нет ни одного) и матрицу соответствия (см. `matrix_gap`).

    Вызывается перед каждым пересчётом оценки — по кнопке, по замечанию специалиста и в
    полном разборе. Разбор от этого дольше, зато «Наши приборы» и «Кто проходит» не
    остаются «не проверен по ТЗ» только потому, что какой-то шаг когда-то не дошёл до
    конца. Сбой шага не отменяет оценку — он называется в итоге задачи."""

    messages: list[str] = []
    has_requirements = db.scalar(
        select(func.count()).select_from(Requirement).where(Requirement.tender_id == tender.id)
    )
    if not has_requirements:
        try:
            messages.append(f"анализ документов: {_run_analysis(db, tender, actor)}")
        except Exception as exc:  # noqa: BLE001 - оценка посчитается и без требований
            db.rollback()
            messages.append(f"анализ документов не выполнен ({exc})")

    reason = matrix_gap(db, tender)
    if reason:
        try:
            messages.append(
                f"матрица достроена ({reason}): "
                f"{_run_evaluation(db, tender, actor, since=_GAP_ONLY)}"
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            messages.append(f"матрица не построена ({reason}): {exc}")
    return messages


def enqueue_gap_repairs(db: Session, *, limit: int = 30) -> int:
    """Ночной обход брешей: открытые закупки с заключением ИИ, у которых матрица не
    построена или устарела (`matrix_gap`), встают на пересчёт оценки — а он сначала
    достраивает матрицу (`prepare_tender`). Закупки, по которым идёт или стоит в очереди
    любая задача, не трогаются."""

    busy = select(BackgroundJob.tender_id).where(
        BackgroundJob.tender_id.is_not(None),
        BackgroundJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
    )
    tenders = db.scalars(
        select(Tender)
        .join(AiProfileScore, AiProfileScore.tender_id == Tender.id)
        .where(
            AiProfileScore.is_current.is_(True),
            (Tender.application_end.is_(None))
            | (Tender.application_end > datetime.now(timezone.utc)),
            Tender.id.not_in(busy),
        )
        .order_by(Tender.application_end.asc().nulls_last())
    )
    queued = 0
    for tender in tenders:
        if queued >= limit:
            break
        if matrix_gap(db, tender) is None:
            continue
        enqueue(db, kind=JobKind.AI_PROFILE_SCORE, tender=tender, actor=None)
        queued += 1
    return queued


def _run_analysis_job(db: Session, tender: Tender, actor: User | None) -> str:
    """Задача «Анализ документов»: после извлечения требований сразу строится матрица —
    иначе новые требования остаются непроверенными до отдельного расчёта."""

    message = _run_analysis(db, tender, actor)
    reason = matrix_gap(db, tender)
    if reason:
        try:
            message += f"; матрица: {_run_evaluation(db, tender, actor, since=_GAP_ONLY)}"
        except Exception as exc:  # noqa: BLE001 - требования уже сохранены
            db.rollback()
            message += f"; матрица не построена: {exc}"
    return message


def _run_profile_score(db: Session, tender: Tender, actor: User | None) -> str:
    """AI-оценка по профилю (раздел 5.5.1 ТЗ).

    Перед оценкой обновляются девять разделов «Дополнительно»: измерение Competencies
    считается именно по ним — обязательные допуски и требования к участнику в перечень
    технических требований почти никогда не попадают. Сбой разбора разделов не отменяет
    оценку: она посчитается по карточке и требованиям, просто менее точно.
    """

    # Имя модели — в сообщение задачи: провайдер переключается в настройках, и по журналу
    # задач иначе не понять, чьи это цифры, когда результаты до и после переключения различаются.
    messages: list[str] = [f"модель: {PROVIDER_LABELS[get_active_provider(db)]}"]
    messages.extend(prepare_tender(db, tender, actor))
    try:
        report_progress("Разделы «Дополнительно»: условия, сроки, требования к участнику…")
        card = tender_card_service.sync_card(db, tender, actor=actor)
        sections = tender_insights.build_extra_sections(db, tender, card, actor=actor)
        if sections:
            tender_insights.store_extra_sections(db, tender, sections)
            messages.append(
                f"разделов «Дополнительно»: {sum(1 for value in sections.values() if value)}"
            )
    except Exception as exc:  # noqa: BLE001 - разделы не обязательны для самой оценки
        messages.append(f"разделы «Дополнительно» не обновлены ({exc})")

    outcome = compute_profile_score(db, tender, actor=actor)
    # Пересчёт учёл все замечания специалистов — висящие «на пересмотре» закрываются им же.
    ai_feedback_service.resolve_pending(db, tender)
    score = outcome.score
    if score is not None and score.overall_score is not None:
        messages.insert(0, f"итоговая оценка: {float(score.overall_score):.0f}%")
    else:
        messages.insert(0, "оценка не посчитана")
    messages.extend(outcome.messages)

    notification_service.notify_high_ai_score(db, tender)
    return "; ".join(messages)


def _run_feedback(db: Session, tender: Tender, actor: User | None) -> str:
    """Пересмотр заключения ИИ по замечаниям тендерного специалиста (28.09.2026).

    Разделы «Дополнительно» не перечитываются: специалист ждёт ответа на своё замечание, а
    документация с прошлого разбора не менялась."""

    prepared = prepare_tender(db, tender, actor)
    message = ai_feedback_service.process_pending(db, tender, actor)
    if prepared:
        message = "; ".join([message, *prepared])
    notification_service.notify_high_ai_score(db, tender)
    return message


def _progressed(job: BackgroundJob, results: dict[str, str]) -> dict:
    """`payload` после пройденного шага: итоги шагов, без счётчика перезапусков — задача
    продвинулась, значит, не она роняет сервер (см. `jobs.recover_interrupted_jobs`)."""

    payload = {**(job.payload or {}), **results}
    payload.pop("restarts", None)
    return payload


def _run_evaluation_job(db: Session, tender: Tender, actor: User | None) -> str:
    """Задача «Расчёт соответствия» — полный пересчёт. После перезапуска сервера
    производители, посчитанные этой же задачей до обрыва, не пересчитываются."""

    return _run_evaluation(db, tender, actor, since=current_job_created_at())


def _run_full_review(db: Session, job: BackgroundJob, actor: User | None) -> str:
    """Полный разбор закупки: анализ документов → матрица соответствия → AI-оценка.

    Обработчик уровня задачи, а не тендера: между шагами в `message` пишется, какой шаг
    идёт, — карточка показывает это вместо безымянного индикатора на минуту-две. Каждый шаг
    изолирован: без требований к товару матрица пропускается (закупка на услуги), сбой
    одного шага не отменяет остальных, и только когда не удались ни анализ, ни оценка,
    задача считается неудавшейся. Итог каждого шага — в `payload`: вкладка «Требования»
    объясняет пустоту итогом именно анализа, а не всей цепочки.
    """

    tender = db.get(Tender, job.tender_id) if job.tender_id else None
    if tender is None:
        raise RuntimeError("Тендер удалён")

    # Итоги шагов, пройденных до перезапуска сервера (см. `jobs.recover_interrupted_jobs`):
    # задача продолжает с оборванного шага, а не повторяет анализ документации заново.
    done = {
        key: value
        for key, value in (job.payload or {}).items()
        if key in {"analysis", "evaluation"} and isinstance(value, str)
    }
    results: dict[str, str] = dict(done)
    failures = 0

    def step(number: int, title: str) -> None:
        # Отменённый пользователем разбор следующий шаг не начинает.
        raise_if_cancelled()
        job.message = f"Шаг {number} из 3: {title}…"
        db.commit()

    if "analysis" not in done or done["analysis"].startswith("не выполнен"):
        step(1, "анализ документов")
        try:
            results["analysis"] = _run_analysis(db, tender, actor)
        except Exception as exc:  # noqa: BLE001 - шаг изолирован, цепочка идёт дальше
            db.rollback()
            logger.warning(f"Полный разбор {tender.external_id}: анализ не выполнен: {exc}")
            results["analysis"] = f"не выполнен: {exc}"
            failures += 1
        job.payload = _progressed(job, results)
        db.commit()

    # Матрица строится по `matrix_gap`, а не по «есть ли требования к товару»: после
    # перезапуска она могла успеть достроиться, а после переанализа — устареть.
    reason = matrix_gap(db, tender)
    if reason:
        step(2, "расчёт соответствия")
        # Две попытки: без матрицы заключение называет наши приборы «не проверен по ТЗ».
        for attempt in (1, 2):
            try:
                results["evaluation"] = _run_evaluation(db, tender, actor, since=_GAP_ONLY)
                break
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning(
                    f"Полный разбор {tender.external_id}: матрица не построена "
                    f"(попытка {attempt}): {exc}"
                )
                results["evaluation"] = f"не выполнен: {exc}"
    elif "evaluation" not in results:
        product_requirements = db.scalar(
            select(func.count())
            .select_from(Requirement)
            .where(
                Requirement.tender_id == tender.id,
                Requirement.kind == RequirementKind.PRODUCT.value,
            )
        )
        results["evaluation"] = (
            "матрица актуальна"
            if product_requirements
            else "пропущен — требований к товару нет (закупка на услуги или работы)"
        )
    job.payload = _progressed(job, results)
    db.commit()

    step(3, "AI-оценка по профилю")
    try:
        results["score"] = _run_profile_score(db, tender, actor)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(f"Полный разбор {tender.external_id}: оценка не посчитана: {exc}")
        results["score"] = f"не посчитана: {exc}"
        failures += 1
    job.payload = _progressed(job, results)
    db.commit()

    summary = (
        f"Анализ: {results['analysis']} | Матрица: {results['evaluation']} | "
        f"Оценка: {results['score']}"
    )
    if failures == 2:
        raise RuntimeError(summary)
    return summary


def _run_sources_poll(db: Session, job: BackgroundJob, actor: User | None) -> str:
    """Опрос площадок по кнопке «Синхронизировать» (баг 17.09.2026: «бесконечная
    синхронизация»). Площадки идут по очереди, и после каждой в `message` задачи пишется
    прогресс — интерфейс показывает его в оверлее, а не крутит безымянный индикатор двадцать
    минут. Итог по каждой площадке остаётся в `payload["results"]`."""

    keys: list[str] = list((job.payload or {}).get("source_keys") or [])
    sources = []
    for key in keys:
        source = get_source_by_key(db, key)
        if source is not None and source.type not in POLL_EXCLUDED_SOURCE_TYPES:
            sources.append(source)
    if not sources:
        return "Нет площадок для опроса"

    results: list[dict] = []
    for index, source in enumerate(sources, start=1):
        prefix = f"Опрос {index} из {len(sources)}: {source.name}"
        job.message = f"{prefix}…"
        db.commit()

        def report(processed: int, created: int, updated: int, prefix: str = prefix) -> None:
            # После каждой порции: сколько уже сохранено — интерфейс показывает это в
            # полосе прогресса и тут же перечитывает список.
            job.message = f"{prefix} — сохранено {processed} (новых {created}, обновлено {updated})"
            db.commit()

        result = poll_source(
            db, source, actor_id=actor.id if actor else None, on_progress=report
        )
        results.append(
            {
                "source_key": result.source_key,
                "source_name": source.name,
                "found": result.found,
                "created": result.created,
                "updated": result.updated,
                "errors": result.errors,
            }
        )
        # JSONB не замечает правку словаря на месте — присваивается новый объект.
        job.payload = {**(job.payload or {}), "results": results}
        db.commit()

    created = sum(r["created"] for r in results)
    updated = sum(r["updated"] for r in results)
    errors = sum(r["errors"] for r in results)
    if created:
        job.message = "ИИ-отбор новых закупок…"
        db.commit()
        check_pending_ai_relevance(db)
    summary = (
        f"Опрошено площадок: {len(results)}; новых закупок {created}, обновлено {updated}"
    )
    if errors:
        summary += f", ошибок {errors}"
    return summary


def _shared_with_twins(handler):
    """После разбора поля, заполненные им (тип конкурса, ОКПД2, регион…), переносятся на
    остальные записи той же закупки — иначе запись из Госплана выпадала бы из фильтров,
    хотя закупка разобрана (29.09.2026, см. `tender_twins`). Сбой переноса разбор не
    отменяет."""

    def run(db: Session, tender: Tender, actor: User | None) -> str:
        message = handler(db, tender, actor)
        try:
            tender_twins.share_fields(db, tender)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - перенос полей вторичен
            db.rollback()
            logger.warning(f"Поля разбора {tender.external_id} не перенесены на двойников: {exc}")
        return message

    return run


def _full_review_shared(db: Session, job: BackgroundJob, actor: User | None) -> str:
    tender = db.get(Tender, job.tender_id) if job.tender_id else None
    message = _run_full_review(db, job, actor)
    if tender is not None:
        try:
            tender_twins.share_fields(db, tender)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - перенос полей вторичен
            db.rollback()
            logger.warning(f"Поля разбора {tender.external_id} не перенесены на двойников: {exc}")
    return message


register_handler(JobKind.TENDER_ANALYSIS, _shared_with_twins(_run_analysis_job))
register_job_handler(JobKind.SOURCES_POLL, _run_sources_poll)
register_handler(JobKind.AI_PROFILE_SCORE, _shared_with_twins(_run_profile_score))
register_handler(JobKind.AI_FEEDBACK, _shared_with_twins(_run_feedback))
register_handler(JobKind.TENDER_EVALUATION, _run_evaluation_job)
register_job_handler(JobKind.TENDER_FULL_REVIEW, _full_review_shared)
