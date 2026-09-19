"""Обработчики фоновых задач: что именно выполняется для каждого вида задачи из
`app/core/jobs.py`. Вынесено из модуля очереди, чтобы очередь не зависела от прикладных
сервисов (иначе `jobs` → `tender_analysis` → `jobs` замкнулись бы в цикл импортов).

Импортируется один раз при старте приложения (`app/main.py`).
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.jobs import register_handler, register_job_handler
from app.models.analysis import Requirement, RequirementKind
from app.models.job import BackgroundJob, JobKind
from app.models.tender import Tender
from app.models.user import User
from app.services import notification_service, tender_card_service, tender_insights
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


def _run_evaluation(db: Session, tender: Tender, actor: User | None) -> str:
    outcome = evaluate_tender(db, tender, actor=actor)

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
    try:
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
    score = outcome.score
    if score is not None and score.overall_score is not None:
        messages.insert(0, f"итоговая оценка: {float(score.overall_score):.0f}%")
    else:
        messages.insert(0, "оценка не посчитана")
    messages.extend(outcome.messages)

    notification_service.notify_high_ai_score(db, tender)
    return "; ".join(messages)


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

    results: dict[str, str] = {}
    failures = 0

    def step(number: int, title: str) -> None:
        job.message = f"Шаг {number} из 3: {title}…"
        db.commit()

    step(1, "анализ документов")
    try:
        results["analysis"] = _run_analysis(db, tender, actor)
    except Exception as exc:  # noqa: BLE001 - шаг изолирован, цепочка идёт дальше
        db.rollback()
        logger.warning(f"Полный разбор {tender.external_id}: анализ не выполнен: {exc}")
        results["analysis"] = f"не выполнен: {exc}"
        failures += 1
    job.payload = {**(job.payload or {}), **results}
    db.commit()

    product_requirements = db.scalar(
        select(func.count())
        .select_from(Requirement)
        .where(
            Requirement.tender_id == tender.id,
            Requirement.kind == RequirementKind.PRODUCT.value,
        )
    )
    if product_requirements:
        step(2, "расчёт соответствия")
        try:
            results["evaluation"] = _run_evaluation(db, tender, actor)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning(f"Полный разбор {tender.external_id}: матрица не построена: {exc}")
            results["evaluation"] = f"не выполнен: {exc}"
    else:
        results["evaluation"] = "пропущен — требований к товару нет (закупка на услуги или работы)"
    job.payload = {**(job.payload or {}), **results}
    db.commit()

    step(3, "AI-оценка по профилю")
    try:
        results["score"] = _run_profile_score(db, tender, actor)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(f"Полный разбор {tender.external_id}: оценка не посчитана: {exc}")
        results["score"] = f"не посчитана: {exc}"
        failures += 1
    job.payload = {**(job.payload or {}), **results}
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


register_handler(JobKind.TENDER_ANALYSIS, _run_analysis)
register_job_handler(JobKind.SOURCES_POLL, _run_sources_poll)
register_handler(JobKind.AI_PROFILE_SCORE, _run_profile_score)
register_handler(JobKind.TENDER_EVALUATION, _run_evaluation)
register_job_handler(JobKind.TENDER_FULL_REVIEW, _run_full_review)
