"""AI-оценка по профилю: формула, прослеживаемость, версионность (раздел 5.5.1 ТЗ).

Проверяется не «модель ответила», а то, что делает с её ответом наш код: как считается
итог при отсутствующей History, что происходит со ссылками на несуществующие требования и
переживает ли предыдущая оценка пересчёт.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.ai_profile import AiProfileScore, Verdict, WeakPointSeverity
from app.models.analysis import Criticality, Requirement
from app.models.company_participation import (
    CompanyParticipation,
    ParticipationOutcome,
    ParticipationSource,
)
from app.models.company_profile import CompanyProfile
from app.models.manufacturer import Manufacturer
from app.models.source import Source
from app.models.tender import RELEVANCE_TO_STAGE, Tender, TenderStage
from app.services import ai_profile_service, company_profile_service
from app.services.ai_profile_service import DimensionAnswer, ResumeAnswer, WeakPointAnswer
from app.services.company_profile_service import CompanyProfileError


def _tender(db, **overrides) -> Tender:
    source = Source(
        key=f"aiprofile_{uuid.uuid4().hex[:8]}",
        name="Площадка для теста AI-оценки",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"AIP-{uuid.uuid4().hex[:6]}",
        title="Поставка приборов учёта электроэнергии",
        currency="RUB",
        okpd2_code="26.51.63.130",
        **overrides,
    )
    db.add(tender)
    db.flush()
    return tender


def _requirement(db, tender, text: str, criticality: str = Criticality.CRITICAL.value):
    requirement = Requirement(tender_id=tender.id, text=text, criticality=criticality)
    db.add(requirement)
    db.flush()
    return requirement


@pytest.fixture()
def filled_profile(db_session) -> CompanyProfile:
    profile = company_profile_service.get_or_create(db_session)
    profile.years_of_experience = 18
    profile.licenses = [{"name": "СРО на проектирование", "number": "СРО-П-001"}]
    profile.past_projects = [
        {"work_type": "Поставка приборов учёта", "customer": "МРСК", "year": 2024}
    ]
    db_session.flush()
    return profile


def test_overall_excludes_history_instead_of_counting_it_as_zero():
    """History без данных исключается из среднего, а не штрафует оценку (раздел 5.5.1 ТЗ)."""

    without_history = ai_profile_service._overall(None, Decimal("80"), Decimal("60"))
    with_history = ai_profile_service._overall(Decimal("0"), Decimal("80"), Decimal("60"))

    assert without_history == Decimal("70.00")
    assert with_history == Decimal("46.67")
    # Ни одного измерения — итога нет вовсе, а не ноль.
    assert ai_profile_service._overall(None, None, None) is None


def test_evidence_drops_references_to_objects_that_were_not_in_the_prompt(
    db_session, filled_profile
):
    """Ссылка на требование, которого в промпте не было, проверить нельзя — она отбрасывается."""

    tender = _tender(db_session)
    first = _requirement(db_session, tender, "Класс точности не хуже 0,5S")
    second = _requirement(db_session, tender, "Поддержка протокола обмена ИВК «Пирамида»")

    answer = DimensionAnswer(
        score=70,
        comment="—",
        requirement_numbers=[1, 2, 99],
        profile_numbers=[1, 42],
    )
    evidence = ai_profile_service._evidence(answer, [first, second], filled_profile)

    refs = {item["ref_id"] for item in evidence}
    assert str(first.id) in refs and str(second.id) in refs
    assert "years_of_experience" in refs  # первая строка профиля — стаж
    assert len(evidence) == 3  # номера 99 и 42 отброшены


def test_weak_points_are_sorted_by_severity_and_normalized():
    items = [
        WeakPointAnswer(severity="minor", text="Мелочь"),
        WeakPointAnswer(severity="что-то своё", text="Неизвестная значимость"),
        WeakPointAnswer(severity="significant", text="Обеспечение контракта 30%"),
        WeakPointAnswer(severity="moderate", text="   "),
    ]
    normalized = ai_profile_service._normalize_weak_points(items)

    assert [item["severity"] for item in normalized] == [
        WeakPointSeverity.SIGNIFICANT.value,
        WeakPointSeverity.MODERATE.value,  # неизвестное значение приведено к «умеренно»
        WeakPointSeverity.MINOR.value,
    ]
    assert all(item["text"] for item in normalized)  # пустой пункт отброшен


def test_compute_profile_score_saves_traceable_current_version(
    db_session, filled_profile, monkeypatch
):
    """Полный проход расчёта на подменённой модели: сохранилась одна текущая оценка со
    ссылками на источники и снимком профиля."""

    tender = _tender(db_session)
    requirement = _requirement(db_session, tender, "Наличие СРО на проектирование")

    def fake_run_structured(db, *, system_prompt, user_text, response_model, temperature=0.0):
        if response_model is DimensionAnswer:
            return DimensionAnswer(
                score=90,
                comment="Предмет закупки — профильная продукция компании.",
                requirement_numbers=[1],
                profile_numbers=[1],
            )
        return ResumeAnswer(
            summary="Поставка приборов учёта для сетевой организации.",
            verdict=Verdict.GO.value,
            weak_points=[WeakPointAnswer(severity="moderate", text="Короткий срок подачи")],
            strategy_verdict="Идти",
            strategy_price="Ориентир — НМЦК минус 7%",
            strategy_first_step="Запросить разъяснения по протоколу обмена",
        )

    monkeypatch.setattr(ai_profile_service, "run_structured", fake_run_structured)

    outcome = ai_profile_service.compute_profile_score(db_session, tender)
    score = outcome.score

    assert score is not None
    assert score.task_score == Decimal("90.00")
    assert score.competencies_score == Decimal("90.00")
    # History исключена из расчёта, а не посчитана нулём.
    assert score.history_score is None
    assert score.overall_score == Decimal("90.00")
    assert score.verdict == Verdict.GO.value
    assert score.recommended_strategy["first_step"].startswith("Запросить")
    assert any(
        item["ref_id"] == str(requirement.id) for item in score.task_evidence
    ), "число должно быть прослеживаемо до конкретного требования"
    assert score.company_profile_snapshot["years_of_experience"] == 18


def test_recalculation_keeps_previous_version(db_session, filled_profile, monkeypatch):
    """Пересчёт гасит прежнюю оценку, а не переписывает её: иначе изменение цифр
    необъяснимо (раздел 7 ТЗ)."""

    tender = _tender(db_session)
    _requirement(db_session, tender, "Класс точности не хуже 0,5S")

    scores = iter([40, 85])

    def fake_run_structured(db, *, system_prompt, user_text, response_model, temperature=0.0):
        if response_model is DimensionAnswer:
            return DimensionAnswer(
                score=next(scores, 85), comment="—", requirement_numbers=[], profile_numbers=[]
            )
        raise RuntimeError("резюме в этом тесте не нужно")

    monkeypatch.setattr(ai_profile_service, "run_structured", fake_run_structured)

    ai_profile_service.compute_profile_score(db_session, tender)
    ai_profile_service.compute_profile_score(db_session, tender)

    all_versions = db_session.scalars(
        select(AiProfileScore).where(AiProfileScore.tender_id == tender.id)
    ).all()
    current = [version for version in all_versions if version.is_current]

    assert len(all_versions) == 2, "история пересчётов сохраняется"
    assert len(current) == 1, "текущая оценка ровно одна"
    # Сбой блока «Резюме» не отменяет измерения — оценка сохранена, вердикт выведен по порогу.
    assert current[0].summary is None
    assert current[0].verdict is not None


def test_score_requires_filled_company_profile(db_session):
    tender = _tender(db_session)
    profile = company_profile_service.get_or_create(db_session)
    profile.years_of_experience = None
    profile.licenses = []
    profile.past_projects = []
    db_session.flush()

    with pytest.raises(CompanyProfileError):
        ai_profile_service.compute_profile_score(db_session, tender)


def test_relevance_status_is_a_working_alias_over_stage(db_session):
    """Внешний контракт сохранён: поле читается и пишется, хотя колонки в БД больше нет."""

    tender = _tender(db_session, stage=TenderStage.UNDER_REVIEW.value)
    assert tender.relevance_status == "confirmed"

    tender.relevance_status = "rejected"
    assert tender.stage == TenderStage.REJECTED.value

    # Подтверждение релевантности не откатывает уже поданную заявку на «на проверке».
    tender.stage = TenderStage.APPLICATION_SUBMITTED.value
    tender.relevance_status = "confirmed"
    assert tender.stage == TenderStage.APPLICATION_SUBMITTED.value

    # И фильтр по старому значению по-прежнему работает на уровне SQL.
    db_session.flush()
    found = db_session.scalars(
        select(Tender).where(
            Tender.id == tender.id, Tender.relevance_status.in_(["confirmed"])
        )
    ).all()
    assert len(found) == 1
    assert RELEVANCE_TO_STAGE["new"] == TenderStage.AI_SELECTED.value


def test_mirtek_profile_is_bound_to_manufacturer(db_session):
    profile = company_profile_service.get_or_create(db_session)
    mirtek = db_session.get(Manufacturer, profile.manufacturer_id)

    assert mirtek is not None and mirtek.is_mirtek


def test_history_counts_wins_when_outcomes_appear(db_session, filled_profile):
    """Измерение History считается сразу, как только у похожих закупок появляются исходы —
    отдельной доработки для этого не потребуется (раздел 5.5.1 ТЗ)."""

    from app.models.analysis import OutcomeSource, TenderOutcome
    from app.models.market import SimilarTender

    tender = _tender(db_session)
    won = _tender(db_session)
    lost = _tender(db_session)
    for other in (won, lost):
        db_session.add(
            SimilarTender(
                tender_id=tender.id, similar_tender_id=other.id, similarity_score=Decimal("0.9")
            )
        )
    mirtek = db_session.scalars(
        select(Manufacturer).where(Manufacturer.is_mirtek.is_(True))
    ).first()
    db_session.add(
        TenderOutcome(
            tender_id=won.id,
            winner_manufacturer_name=mirtek.brand_name or mirtek.legal_name,
            source=OutcomeSource.EIS_PROTOCOL.value,
        )
    )
    db_session.add(
        TenderOutcome(
            tender_id=lost.id,
            winner_manufacturer_name="Конкурент",
            source=OutcomeSource.EIS_PROTOCOL.value,
        )
    )
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score == Decimal("50.00")
    assert "1 раз" in comment
    assert len(evidence) == 2 and all(item["type"] == "similar_tender" for item in evidence)


def test_history_is_null_when_similar_tenders_have_no_outcomes(db_session):
    """Похожие закупки есть, но ни наших участий, ни известных исходов по ним нет.

    После уточнения 03.09.2026 первым источником History стали `company_participations`,
    поэтому текст «нет данных» подсказывает именно синхронизацию истории участий.
    """

    from app.models.market import SimilarTender

    tender = _tender(db_session)
    other = _tender(db_session)
    db_session.add(
        SimilarTender(
            tender_id=tender.id, similar_tender_id=other.id, similarity_score=Decimal("0.88")
        )
    )
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score is None and evidence == []
    assert "собственных участий по ним нет" in comment
    assert "История участий" not in comment or "Моя компания" in comment


def _participation(
    db, *, outcome: str, tender_id=None, customer_name=None, source=None
):
    mirtek = db.scalars(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True))).first()
    record = CompanyParticipation(
        manufacturer_id=mirtek.id,
        tender_id=tender_id,
        external_tender_id=f"EXT-{uuid.uuid4().hex[:8]}",
        tender_title="Поставка приборов учёта",
        customer_name=customer_name,
        outcome=outcome,
        source=source or ParticipationSource.MANUAL.value,
    )
    db.add(record)
    db.flush()
    return record


def test_history_counts_own_participations_in_similar_tenders(db_session):
    """History считается по нашим участиям в похожих закупках (раздел 5.5.1 ТЗ)."""

    from app.models.market import SimilarTender

    tender = _tender(db_session)
    won_tender = _tender(db_session)
    lost_tender = _tender(db_session)
    for other in (won_tender, lost_tender):
        db_session.add(
            SimilarTender(
                tender_id=tender.id, similar_tender_id=other.id, similarity_score=Decimal("0.9")
            )
        )
    _participation(db_session, outcome=ParticipationOutcome.WON.value, tender_id=won_tender.id)
    _participation(db_session, outcome=ParticipationOutcome.LOST.value, tender_id=lost_tender.id)
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score == Decimal("50.00")
    assert len(evidence) == 2
    assert all(item["type"] == "company_participation" for item in evidence)
    assert "побед — 1" in comment


def test_history_uses_participations_with_the_same_customer(db_session):
    """Прошлый заход к тому же заказчику считается, даже когда эмбеддинги ещё не посчитаны.

    Иначе History молчала бы до появления похожих тендеров — а самый прямой ответ на вопрос
    «выигрывали ли мы у этого заказчика» уже лежит в истории участий.
    """

    tender = _tender(db_session, customer_name="МУП «Водоканал» г. Ростова")
    _participation(
        db_session,
        outcome=ParticipationOutcome.WON.value,
        customer_name="МУП «Водоканал» г. Ростова",
    )
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score == Decimal("100.00")
    assert len(evidence) == 1 and evidence[0]["type"] == "company_participation"
    assert "тому же заказчику" in comment


def test_history_keeps_disqualifications_apart_from_losses(db_session):
    """Снятие с торгов остаётся в знаменателе, но называется отдельно (раздел 7 ТЗ).

    Контракт мы не получили — значит, это не победа; но лечится оно оформлением заявки, а не
    ценой, и слитый счётчик подсказал бы неверное действие.
    """

    tender = _tender(db_session, customer_name="ГУП «Энергосбыт»")
    _participation(
        db_session, outcome=ParticipationOutcome.WON.value, customer_name="ГУП «Энергосбыт»"
    )
    _participation(
        db_session,
        outcome=ParticipationOutcome.DISQUALIFIED.value,
        customer_name="ГУП «Энергосбыт»",
    )
    db_session.flush()

    score, comment, _ = ai_profile_service._history_dimension(db_session, tender)

    assert score == Decimal("50.00")
    assert "Снятий с торгов" in comment


def test_history_ignores_participations_with_unknown_outcome(db_session):
    """Неизвестный исход не идёт в знаменатель: неполнота данных — не череда поражений."""

    tender = _tender(db_session, customer_name="АО «Горсети»")
    _participation(
        db_session, outcome=ParticipationOutcome.UNKNOWN.value, customer_name="АО «Горсети»"
    )
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score is None and evidence == []
    assert "не известен исход" in comment


def test_history_refuses_win_rate_on_wins_only_source(db_session):
    """Выборка целиком из реестра контрактов ЕИС не даёт доли побед (решение 04.09.2026).

    В реестре лежат только заключённые контракты, поэтому 100% там получается по устройству
    источника, а не по заслугам компании. Это зеркальное отражение правила «ноль ≠ нет
    данных»: цифра по заведомо однобокой выборке врёт так же, только в другую сторону.
    Сами победы при этом не теряются — они уходят в комментарий и в evidence.
    """

    tender = _tender(db_session, customer_name="АО «Электросети Юга»")
    for _ in range(3):
        _participation(
            db_session,
            outcome=ParticipationOutcome.WON.value,
            customer_name="АО «Электросети Юга»",
            source=ParticipationSource.EIS_CONTRACTS.value,
        )
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score is None
    assert len(evidence) == 3
    assert all(item["type"] == "company_participation" for item in evidence)
    assert "Подтверждённых побед по этой закупке: 3" in comment
    assert "реестра контрактов ЕИС" in comment


def test_history_computes_once_a_loss_is_added_by_hand(db_session):
    """Одна внесённая руками проигранная закупка — и доля побед снова считается.

    Именно так пользователь выводит измерение из состояния «недостаточно данных»: выборка
    перестаёт быть однобокой, потому что в ней появился источник, знающий о поражениях.
    """

    tender = _tender(db_session, customer_name="АО «Ставропольэнерго»")
    _participation(
        db_session,
        outcome=ParticipationOutcome.WON.value,
        customer_name="АО «Ставропольэнерго»",
        source=ParticipationSource.EIS_CONTRACTS.value,
    )
    _participation(
        db_session,
        outcome=ParticipationOutcome.LOST.value,
        customer_name="АО «Ставропольэнерго»",
        source=ParticipationSource.MANUAL.value,
    )
    db_session.flush()

    score, comment, evidence = ai_profile_service._history_dimension(db_session, tender)

    assert score == Decimal("50.00")
    assert len(evidence) == 2
    assert "побед — 1" in comment
