"""Оценка вердиктов матрицы специалистом (23.10.2026, раздел 70 ARCHITECTURE.md).

Исправление обязано: сразу поменять ячейку и процент, пережить пересчёт матрицы и повторный
анализ документации, вернуть ответ модели при отзыве — и уйти прецедентом в запрос к модели
по похожему требованию другой закупки, но только тому производителю, к которому относится.
"""

from __future__ import annotations

import pytest

import app.services.compliance_service as compliance_module
from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceSource,
    ComplianceStatus,
    WinPercentage,
)
from app.models.compliance_review import ComplianceVerdictReview, ReviewReason
from app.models.user import User
from app.services import compliance_review_service
from app.services.compliance_review_service import ReviewError
from app.services.compliance_service import ComplianceResult, evaluate_tender

from tests.test_compliance_service import (
    _add_requirement,
    _make_manufacturer,
    _make_tender,
    _verdict,
)

# Администратора создаёт бутстрап приложения — без `client` в одиночном прогоне его нет.
pytestmark = pytest.mark.usefixtures("client")

PYRAMID = (
    "Приборы учёта должны быть включены в реестр поддерживаемого программным комплексом "
    "«Пирамида 2.0» оборудования с опросом ICCID SIM-карты"
)
PYRAMID_OTHER_WORDING = (
    "Приборы учёта включены в реестр оборудования, поддерживаемого программным комплексом "
    "«Пирамида 2.0», с опросом ICCID SIM-карты и уровня сигнала"
)


def _model_says(monkeypatch, status: str, prompts: list[str] | None = None) -> None:
    def fake(db, **kwargs):
        if prompts is not None:
            prompts.append(kwargs["user_text"])
        return ComplianceResult(
            verdicts=[
                _verdict(1, status, source="software", explanation="В списке Пирамиды есть", confidence=0.9)
            ]
        )

    monkeypatch.setattr(compliance_module, "run_structured", fake)


def _entry(db_session, requirement, manufacturer) -> ComplianceMatrixEntry:
    return (
        db_session.query(ComplianceMatrixEntry)
        .filter(
            ComplianceMatrixEntry.requirement_id == requirement.id,
            ComplianceMatrixEntry.manufacturer_id == manufacturer.id,
        )
        .one()
    )


def _percentage(db_session, tender, manufacturer) -> WinPercentage:
    return (
        db_session.query(WinPercentage)
        .filter(
            WinPercentage.tender_id == tender.id,
            WinPercentage.manufacturer_id == manufacturer.id,
            WinPercentage.is_current.is_(True),
        )
        .one()
    )


def _correct(db_session, tender, requirement, manufacturer, user, *, reason=ReviewReason.FACT.value):
    return compliance_review_service.create(
        db_session,
        tender,
        requirement_id=requirement.id,
        manufacturer_id=manufacturer.id,
        kind="disagree",
        corrected_status=ComplianceStatus.NOT_MEETS.value,
        reason=reason,
        comment="В списке Пирамиды 2.0 этих моделей нет, проверено на сайте АСТЭК",
        actor=user,
    )


def test_correction_overrides_cell_and_percentage_and_survives_recalculation(
    db_session, admin_user: User, monkeypatch
):
    tender = _make_tender(db_session)
    requirement = _add_requirement(db_session, tender, text=PYRAMID)
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    _model_says(monkeypatch, ComplianceStatus.MEETS.value)
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)
    assert _percentage(db_session, tender, manufacturer).percentage == 100

    review = _correct(db_session, tender, requirement, manufacturer, admin_user)

    entry = _entry(db_session, requirement, manufacturer)
    assert entry.status == ComplianceStatus.NOT_MEETS.value
    assert entry.source == ComplianceSource.HUMAN_REVIEW.value
    assert review.model_status == ComplianceStatus.MEETS.value
    current = _percentage(db_session, tender, manufacturer)
    assert current.percentage == 0
    assert current.verdict == "fails"

    # Модель упорствует — пересчёт всё равно оставляет исправление.
    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)
    assert outcome.human_overrides >= 1
    assert _entry(db_session, requirement, manufacturer).status == ComplianceStatus.NOT_MEETS.value
    assert _percentage(db_session, tender, manufacturer).verdict == "fails"


def test_withdraw_restores_model_verdict(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    requirement = _add_requirement(db_session, tender, text=PYRAMID)
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    _model_says(monkeypatch, ComplianceStatus.MEETS.value)
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)
    review = _correct(db_session, tender, requirement, manufacturer, admin_user)

    compliance_review_service.withdraw(db_session, tender, review.id, admin_user)

    entry = _entry(db_session, requirement, manufacturer)
    assert entry.status == ComplianceStatus.MEETS.value
    assert entry.source == ComplianceSource.UPPER_SOFTWARE.value
    assert _percentage(db_session, tender, manufacturer).percentage == 100
    assert compliance_review_service.find_precedents(db_session, manufacturer, [requirement]) == []


def test_correction_finds_requirement_after_reanalysis(db_session, admin_user: User, monkeypatch):
    """Повторный анализ удаляет требования — исправление находит новое по формулировке."""

    tender = _make_tender(db_session)
    requirement = _add_requirement(db_session, tender, text=PYRAMID)
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    _model_says(monkeypatch, ComplianceStatus.MEETS.value)
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)
    review = _correct(db_session, tender, requirement, manufacturer, admin_user)

    db_session.query(ComplianceMatrixEntry).filter(
        ComplianceMatrixEntry.requirement_id == requirement.id
    ).delete()
    db_session.delete(requirement)
    db_session.commit()
    db_session.refresh(review)
    assert review.requirement_id is None

    fresh = _add_requirement(db_session, tender, text=f"  {PYRAMID.upper()} ")
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    assert _entry(db_session, fresh, manufacturer).status == ComplianceStatus.NOT_MEETS.value
    db_session.refresh(review)
    assert review.requirement_id == fresh.id


def test_correction_becomes_precedent_for_similar_requirement(
    db_session, admin_user: User, monkeypatch
):
    first = _make_tender(db_session)
    requirement = _add_requirement(db_session, first, text=PYRAMID)
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    other = _make_manufacturer(db_session, with_catalog=True)
    _model_says(monkeypatch, ComplianceStatus.MEETS.value)
    evaluate_tender(db_session, first, actor=admin_user, use_manual_fallback=False)
    _correct(db_session, first, requirement, manufacturer, admin_user)

    second = _make_tender(db_session)
    _add_requirement(db_session, second, text=PYRAMID_OTHER_WORDING)
    _add_requirement(db_session, second, text="Гарантийный срок не менее 5 лет")
    prompts: list[str] = []
    _model_says(monkeypatch, ComplianceStatus.MEETS.value, prompts)
    outcome = evaluate_tender(db_session, second, actor=admin_user, use_manual_fallback=False)

    own = [text for text in prompts if manufacturer.brand_name in text.split("\n", 1)[0]]
    foreign = [text for text in prompts if other.brand_name in text.split("\n", 1)[0]]
    assert any("ИСПРАВЛЕНИЯ СПЕЦИАЛИСТОВ" in text and "проверено на сайте АСТЭК" in text for text in own)
    # Неверный факт о приборе — чужому производителю не прецедент.
    assert foreign and not any("ИСПРАВЛЕНИЯ СПЕЦИАЛИСТОВ" in text for text in foreign)
    assert outcome.precedents_used >= 1


def test_misread_requirement_is_precedent_for_every_manufacturer(db_session, admin_user: User, monkeypatch):
    first = _make_tender(db_session)
    requirement = _add_requirement(db_session, first, text=PYRAMID)
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    other = _make_manufacturer(db_session, with_catalog=True)
    _model_says(monkeypatch, ComplianceStatus.MEETS.value)
    evaluate_tender(db_session, first, actor=admin_user, use_manual_fallback=False)
    _correct(db_session, first, requirement, manufacturer, admin_user, reason=ReviewReason.REQUIREMENT.value)

    second = _make_tender(db_session)
    fresh = _add_requirement(db_session, second, text=PYRAMID_OTHER_WORDING)
    text, used = compliance_review_service.precedents_block(db_session, other, [fresh], tender_id=second.id)
    assert used == 1 and manufacturer.brand_name in text and "неверно понято требование" in text

    unrelated = _add_requirement(db_session, second, text="Гарантийный срок не менее 5 лет")
    assert compliance_review_service.precedents_block(db_session, other, [unrelated])[1] == 0


def test_review_validation_and_agreement(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    requirement = _add_requirement(db_session, tender, text=PYRAMID)
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    _model_says(monkeypatch, ComplianceStatus.MEETS.value)
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    common = dict(requirement_id=requirement.id, manufacturer_id=manufacturer.id, actor=admin_user)
    with pytest.raises(ReviewError, match="в чём ошибка"):
        compliance_review_service.create(
            db_session, tender, kind="disagree", corrected_status="not_meets", reason="fact",
            comment="нет", **common,
        )
    with pytest.raises(ReviewError, match="и так"):
        compliance_review_service.create(
            db_session, tender, kind="disagree", corrected_status="meets", reason="fact",
            comment="всё верно, но не так", **common,
        )

    # Согласие записывается и ничего не меняет; повторное — заменяет своё прежнее.
    for _ in range(2):
        compliance_review_service.create(
            db_session, tender, kind="agree", corrected_status=None, reason=None, comment=None, **common
        )
    current = (
        db_session.query(ComplianceVerdictReview)
        .filter(
            ComplianceVerdictReview.requirement_id == requirement.id,
            ComplianceVerdictReview.is_current.is_(True),
        )
        .all()
    )
    assert [item.kind for item in current] == ["agree"]
    assert _entry(db_session, requirement, manufacturer).status == ComplianceStatus.MEETS.value

    # Исправление гасит согласия с прежним вердиктом.
    _correct(db_session, tender, requirement, manufacturer, admin_user)
    kinds = [
        item.kind
        for item in db_session.query(ComplianceVerdictReview).filter(
            ComplianceVerdictReview.requirement_id == requirement.id,
            ComplianceVerdictReview.is_current.is_(True),
        )
    ]
    assert kinds == ["disagree"]
