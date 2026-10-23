"""Возражения к заключению ИИ — прецеденты для похожих закупок (23.10.2026, раздел 71
ARCHITECTURE.md).

Учтённое несогласие по одной закупке должно уйти во все запросы AI-оценки похожей закупки и
быть названо в её заключении, но не попасть в непохожую, не уйти неучтённым и не уйти, если
автор пометил его «только для этой закупки».
"""

from __future__ import annotations

import pytest

from app.models.user import User, UserRole
from app.services import ai_conclusion_service, ai_feedback_service, ai_profile_service
from tests.test_ai_conclusion import _fake, _fit, _patch, _user
from tests.test_ai_profile import _requirement, _tender, filled_profile  # noqa: F401

CUSTOMER = "ПАО «Россети Тюмень»"
REQUIREMENTS = (
    "Счётчик трёхфазный трансформаторного включения, класс точности 0,5S",
    "Интерфейсы RS-485 и GSM, протокол СПОДЭС",
    "Включение в реестр промышленной продукции по ПП 719",
    "Интеграция в ПО «Пирамида 2.0»",
)
REMARK = "У МИРТЕК-32 есть запись в ПП 719 до 2027 года — проходим"


def _meter_tender(db, title: str):
    tender = _tender(db, title=title, customer_name=CUSTOMER)
    for text in REQUIREMENTS:
        _requirement(db, tender, text)
    return tender


def _applied_remark(db, monkeypatch, tender, text: str = REMARK):
    _patch(monkeypatch, _fake(_fit()))
    ai_profile_service.compute_profile_score(db, tender)
    monkeypatch.setattr(ai_feedback_service, "enqueue", lambda db, **kw: None)
    feedback, _ = ai_feedback_service.create(db, tender, kind="disagree", text=text, actor=_user(db))
    ai_feedback_service.process_pending(db, tender, _user(db))
    db.refresh(feedback)
    return feedback


def test_remark_reaches_every_call_of_similar_tender(db_session, filled_profile, monkeypatch):
    source = _meter_tender(db_session, "Поставка счётчиков электроэнергии трёхфазных")
    feedback = _applied_remark(db_session, monkeypatch, source)

    similar = _meter_tender(db_session, "Поставка трёхфазных приборов учёта электроэнергии")
    prompts: list[str] = []
    _patch(monkeypatch, _fake(_fit(), prompts))
    outcome = ai_profile_service.compute_profile_score(db_session, similar)

    assert prompts and all(REMARK in text for text in prompts)
    # Прецедент — не замечание к этой закупке: отвечать на него модель не обязана.
    assert not any("ЗАМЕЧАНИЯ СПЕЦИАЛИСТОВ" in text for text in prompts)
    precedents = outcome.score.conclusion["precedents"]
    assert [item["feedback_id"] for item in precedents] == [str(feedback.id)]
    assert precedents[0]["tender_external_id"] == source.external_id
    assert any("похожим закупкам: 1" in message for message in outcome.messages)


def test_remark_skips_unrelated_unapplied_and_private(db_session, filled_profile, monkeypatch):
    source = _meter_tender(db_session, "Поставка счётчиков электроэнергии трёхфазных")
    feedback = _applied_remark(db_session, monkeypatch, source)
    similar = _meter_tender(db_session, "Поставка трёхфазных приборов учёта электроэнергии")

    unrelated = _tender(
        db_session, title="Уборка снега на территории подстанций", customer_name="ООО «Чистый двор»"
    )
    _requirement(db_session, unrelated, "Вывоз снега спецтехникой в течение суток")
    assert ai_conclusion_service.precedents_block(db_session, unrelated) == (None, [])
    # Своё замечание закупка видит как замечание, а не как прецедент.
    assert ai_conclusion_service.precedents_block(db_session, source) == (None, [])

    # Только для этой закупки — автор выключил перенос.
    stranger = User(username="precedent_stranger", password_hash="x", full_name="Другой")
    db_session.add(stranger)
    db_session.flush()
    with pytest.raises(ai_feedback_service.FeedbackError, match="автор"):
        ai_feedback_service.set_precedent(db_session, source, feedback.id, share=False, actor=stranger)
    ai_feedback_service.set_precedent(db_session, source, feedback.id, share=False, actor=_user(db_session))
    assert ai_conclusion_service.precedents_block(db_session, similar) == (None, [])

    # Администратор может вернуть.
    stranger.role = UserRole.ADMIN.value
    ai_feedback_service.set_precedent(db_session, source, feedback.id, share=True, actor=stranger)
    assert ai_conclusion_service.precedents_block(db_session, similar)[1]

    # Неучтённое (на пересмотре) — не прецедент: «после» ещё нет.
    feedback.status = "pending"
    db_session.flush()
    assert ai_conclusion_service.precedents_block(db_session, similar) == (None, [])
