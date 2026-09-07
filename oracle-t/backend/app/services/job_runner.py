"""Обработчики фоновых задач: что именно выполняется для каждого вида задачи из
`app/core/jobs.py`. Вынесено из модуля очереди, чтобы очередь не зависела от прикладных
сервисов (иначе `jobs` → `tender_analysis` → `jobs` замкнулись бы в цикл импортов).

Импортируется один раз при старте приложения (`app/main.py`).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.jobs import register_handler
from app.models.job import JobKind
from app.models.tender import Tender
from app.models.user import User
from app.services import notification_service, tender_card_service, tender_insights
from app.services.ai_profile_service import compute_profile_score
from app.services.compliance_service import evaluate_tender
from app.services.document_service import sync_tender_documents
from app.services.similarity_service import refresh_similar
from app.services.tender_analysis import analyze_tender


def _run_analysis(db: Session, tender: Tender, actor: User | None) -> str:
    # Документы должны быть скачаны и распознаны — иначе анализировать нечего, кроме
    # наименования (раздел 5.2 ТЗ). Раньше это делал HTTP-эндпоинт до вызова анализа.
    sync_tender_documents(db, tender)
    outcome = analyze_tender(db, tender, actor=actor)

    parts = [f"требований сохранено: {outcome.requirements_saved}"]
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

    messages: list[str] = []
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


register_handler(JobKind.TENDER_ANALYSIS, _run_analysis)
register_handler(JobKind.AI_PROFILE_SCORE, _run_profile_score)
register_handler(JobKind.TENDER_EVALUATION, _run_evaluation)
