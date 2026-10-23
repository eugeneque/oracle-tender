"""Заключение ИИ и ответы специалистов (28.09.2026)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.ai_feedback import AiScoreFeedback, FeedbackStatus
from app.models.ai_profile import Verdict
from app.models.manufacturer import Manufacturer, Product
from app.models.user import User
from app.services import ai_conclusion_service, ai_feedback_service, ai_profile_service
from app.services.ai_conclusion_service import (
    CompetitorAnswer,
    FitAnswer,
    PlanAnswer,
    ProductAnswer,
    RiskAnswer,
)
from app.services.ai_profile_service import CompetenciesAnswer, TaskAnswer
from tests.test_ai_profile import ALL_MET, _requirement, _task_answer, _tender, filled_profile  # noqa: F401


def _mirtek_product(db, name: str) -> Product:
    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    product = Product(manufacturer_id=mirtek.id, model_name=name)
    db.add(product)
    db.flush()
    return product


def _fake(fit: FitAnswer, captured: list[str] | None = None):
    def run(db, *, system_prompt, user_text, response_model, temperature=0.0):
        if captured is not None:
            captured.append(user_text)
        if response_model is TaskAnswer:
            return _task_answer(ALL_MET)
        if response_model is CompetenciesAnswer:
            return CompetenciesAnswer(requirements=[], comment="Требований к участнику нет.")
        if response_model is FitAnswer:
            return fit
        if response_model is PlanAnswer:
            return PlanAnswer(
                approach="Заходим счётчиком МИР С-05.",
                price="НМЦК не указана — считать от прошлых контрактов заказчика.",
                steps=["Запросить у заказчика НМЦК", ""],
                risks=[
                    RiskAnswer(category="price", severity="minor", text="Нет НМЦК.", mitigation="Запросить."),
                    RiskAnswer(category="мусор", severity="significant", text="Срок 2 дня.", mitigation="Готовить сразу."),
                ],
            )
        raise AssertionError(f"неожиданный вызов {response_model}")

    return run


def _patch(monkeypatch, runner) -> None:
    monkeypatch.setattr(ai_profile_service, "run_structured", runner)
    monkeypatch.setattr(ai_conclusion_service, "run_structured", runner)


def _fit(**overrides) -> FitAnswer:
    fields = {
        "fit": "fit",
        "participate": True,
        "headline": "Проходим счётчиком МИР С-05.",
        "rationale": "Предмет — наши приборы.",
        "our_products": [
            ProductAnswer(model="мир с-05", status="fits", note="всё выполнено"),
            ProductAnswer(model="Выдуманный-9000", status="fits", note="нет в каталоге"),
        ],
        "competitors": [
            CompetitorAnswer(manufacturer="Несуществующий завод", model="", status="fits", note="")
        ],
        "feedback_response": "",
    }
    fields.update(overrides)
    return FitAnswer(**fields)


def test_conclusion_names_only_catalog_devices_and_needs_matrix_for_fit(
    db_session, filled_profile, monkeypatch
):
    tender = _tender(db_session)
    product = _mirtek_product(db_session, "МИР С-05.10-230-5(80)")
    _patch(monkeypatch, _fake(_fit()))

    score = ai_profile_service.compute_profile_score(db_session, tender).score
    conclusion = score.conclusion

    # Матрица не построена — «подходим» без проверки по ТЗ не выдаётся.
    assert conclusion["fit"] == "fit_with_caveats"
    assert score.verdict == Verdict.GO_WITH_RESERVATIONS.value
    # Прибор сверен с каталогом: выдуманный выброшен, найденный назван полным именем
    # и помечен как не проверенный по ТЗ.
    assert [item["model"] for item in conclusion["our_products"]] == [product.model_name]
    assert conclusion["our_products"][0]["status"] == "unchecked"
    assert conclusion["competitors"] == []
    # Риски отсортированы по значимости, неизвестная категория — «прочее».
    assert [risk["severity"] for risk in conclusion["risks"]] == ["significant", "minor"]
    assert conclusion["risks"][0]["category"] == "other"
    assert conclusion["strategy"]["steps"] == ["Запросить у заказчика НМЦК"]
    metrics = {item["key"]: item for item in conclusion["metrics"]}
    assert metrics["price"]["value"] == "не указана"
    assert metrics["competencies"]["value"] == "не требуются"
    # Прежние поля заполнены из заключения — их читают список и уведомления.
    assert score.summary.startswith("Проходим счётчиком")
    assert score.weak_points[0]["text"] == "Срок 2 дня."
    assert ai_profile_service.serialize(db_session, score)["conclusion"]["fit_label"]


def test_not_fit_means_no_go_whatever_the_model_says_about_participation(
    db_session, filled_profile, monkeypatch
):
    tender = _tender(db_session)
    _patch(monkeypatch, _fake(_fit(fit="not_fit", participate=True, our_products=[])))

    score = ai_profile_service.compute_profile_score(db_session, tender).score

    assert score.verdict == Verdict.NO_GO.value
    assert score.decision is False


def _user(db) -> User:
    user = db.scalar(select(User).where(User.username == "conclusion_specialist"))
    if user is None:
        user = User(username="conclusion_specialist", password_hash="x", full_name="Тендерный специалист")
        db.add(user)
        db.flush()
    return user


def test_disagreement_is_reviewed_with_before_and_after(db_session, filled_profile, monkeypatch):
    tender = _tender(db_session)
    _mirtek_product(db_session, "МИР С-05.10-230-5(80)")
    _patch(monkeypatch, _fake(_fit()))
    ai_profile_service.compute_profile_score(db_session, tender)

    enqueued: list = []
    monkeypatch.setattr(ai_feedback_service, "enqueue", lambda db, **kw: enqueued.append(kw) or None)

    with pytest.raises(ai_feedback_service.FeedbackError):
        ai_feedback_service.create(db_session, tender, kind="disagree", text=" ", actor=_user(db_session))

    feedback, _ = ai_feedback_service.create(
        db_session,
        tender,
        kind="disagree",
        text="В ТЗ требуется ПП 719, у МИР С-05 записи нет",
        actor=_user(db_session),
    )
    assert feedback.status == FeedbackStatus.PENDING.value
    assert feedback.before["headline"] == "Проходим счётчиком МИР С-05."
    assert len(enqueued) == 1

    prompts: list[str] = []
    _patch(
        monkeypatch,
        _fake(
            _fit(
                fit="not_fit",
                participate=False,
                headline="Не проходим: нет записи в ПП 719.",
                feedback_response="Согласен: без записи в ПП 719 заявку отклонят.",
            ),
            prompts,
        ),
    )
    message = ai_feedback_service.process_pending(db_session, tender, _user(db_session))

    db_session.refresh(feedback)
    assert "Учтено замечаний: 1" in message
    assert feedback.status == FeedbackStatus.APPLIED.value
    assert feedback.after["headline"] == "Не проходим: нет записи в ПП 719."
    assert feedback.after["verdict"] == Verdict.NO_GO.value
    assert feedback.ai_response.startswith("Согласен")
    # Замечание ушло во все вызовы пересчёта, не только в заключение.
    assert all("ПП 719, у МИР С-05 записи нет" in text for text in prompts)

    rows = ai_feedback_service.list_for_tender(db_session, tender)
    assert rows[0]["text"].startswith("В ТЗ требуется")
    items, total = ai_feedback_service.list_all(db_session, kind="disagree", limit=50, offset=0)
    assert total >= 1 and any(item["tender_id"] == tender.id for item in items)


def test_agreement_is_recorded_without_review(db_session, filled_profile, monkeypatch):
    tender = _tender(db_session)
    _patch(monkeypatch, _fake(_fit()))
    ai_profile_service.compute_profile_score(db_session, tender)
    monkeypatch.setattr(ai_feedback_service, "enqueue", lambda db, **kw: pytest.fail("не должно"))

    feedback, job = ai_feedback_service.create(
        db_session, tender, kind="agree", text=None, actor=_user(db_session)
    )

    assert job is None
    assert feedback.status == FeedbackStatus.RECORDED.value
    assert db_session.scalar(select(AiScoreFeedback).where(AiScoreFeedback.id == feedback.id))


def test_national_regime_limits_our_devices_to_registry_records(
    db_session, filled_profile, monkeypatch
):
    """SBR003-260026413800036.1 (замечание 22.10.2026): по ПП 1875 к поставке допускаются
    только приборы с записью в реестре промпродукции, а заключение предлагало W3. Снятая с
    производства модель из «Описания типа» не предлагается вовсе."""

    from app.models.manufacturer import ProductStatus
    from app.models.registry_record import ProductRegistryRecord

    tender = _tender(db_session, title="Приобретение, установка и пуско-наладка приборов учёта", tender_type="complex")
    _requirement(db_session, tender, "Применяется национальный режим в соответствии с ПП РФ № 1875")
    d17 = _mirtek_product(db_session, "МИРТЕК-12-РУ-D17-2")
    _mirtek_product(db_session, "МИРТЕК-12-РУ-W3")
    retired = _mirtek_product(db_session, "МИРТЕК-12-РУ-W2")
    retired.status = ProductStatus.DISCONTINUED.value
    db_session.add(ProductRegistryRecord(product_id=d17.id, registry="industrial_products", record_number="10\\1\\1/2024"))
    db_session.flush()

    captured: list[str] = []
    fit = _fit(our_products=[
        ProductAnswer(model="МИРТЕК-12-РУ-W3", status="fits", note="ток подходит"),
        ProductAnswer(model="МИРТЕК-12-РУ-W2", status="fits", note="есть в описании типа"),
        ProductAnswer(model="МИРТЕК-12-РУ-D17-2", status="fits", note="всё выполнено"),
    ])
    _patch(monkeypatch, _fake(fit, captured))

    conclusion = ai_profile_service.compute_profile_score(db_session, tender).score.conclusion

    rows = [(item["model"], item["status"]) for item in conclusion["our_products"]]
    assert rows == [("МИРТЕК-12-РУ-D17-2", "unchecked"), ("МИРТЕК-12-РУ-W3", "not_fits")]
    assert "ПП 1875" in conclusion["our_products"][1]["note"]
    prompt = next(text for text in captured if "ОГРАНИЧЕНИЕ ДОПУСКА" in text)
    assert "Наши приборы с записью: МИРТЕК-12-РУ-D17-2" in prompt


def test_national_regime_does_not_cut_devices_inside_cabinets(db_session, filled_profile, monkeypatch):
    """Прибор в составе шкафа учёта: ограничение относится к шкафу — прибор не отсекается."""

    from app.models.registry_record import ProductRegistryRecord

    tender = _tender(db_session, title="Поставка шкафов учёта ВШУ с приборами учёта")
    _requirement(db_session, tender, "Национальный режим (ПП РФ № 1875)")
    d17 = _mirtek_product(db_session, "МИРТЕК-12-РУ-D17-2")
    _mirtek_product(db_session, "МИРТЕК-12-РУ-W3")
    db_session.add(ProductRegistryRecord(product_id=d17.id, registry="industrial_products"))
    db_session.flush()

    captured: list[str] = []
    fit = _fit(our_products=[ProductAnswer(model="МИРТЕК-12-РУ-W3", status="fits", note="")])
    _patch(monkeypatch, _fake(fit, captured))

    conclusion = ai_profile_service.compute_profile_score(db_session, tender).score.conclusion

    assert [item["status"] for item in conclusion["our_products"]] == ["unchecked"]
    assert any("состав шкафа" in text for text in captured)


def test_purchase_kinds_block_names_every_type_and_unparsed_phase():
    """32616469643 (замечание 22.10.2026): в документации и однофазные, и трёхфазные, а
    заключение по конкурентам говорило «подходит только однофазный»."""

    from app.models.analysis import Requirement
    from app.models.tender import Tender
    from app.services.ai_conclusion_service import purchase_kinds_block

    tender = Tender(title="Рамочное соглашение на поставку ПУ", meter_kinds=["1ph_direct_din"])
    requirements = [Requirement(text="Класс точности 1", position="1. Однофазный ПУ")]
    block = purchase_kinds_block(tender, requirements, "…однофазные и трёхфазные приборы учёта…")

    assert block is not None
    assert "1. Однофазный ПУ" in block
    assert "трёхфазные приборы, но требований к ним не извлечено" in block
    assert purchase_kinds_block(Tender(title="Услуги"), [], "") is None
