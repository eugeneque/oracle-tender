"""Позиции, условия поставки и условия договора одним запросом (12.11.2026, раздел 104)."""

from __future__ import annotations

from app.services import delivery_terms_service, document_facts_service as service, tender_positions
from app.services.contract_conditions_service import ConditionsAnswer
from app.services.tender_positions import PositionAnswer
from tests.test_delivery_terms import _answer as _delivery_answer
from tests.test_delivery_terms import _tender


def _facts() -> service.BitrixFactsAnswer:
    return service.BitrixFactsAnswer(
        positions=[
            PositionAnswer(
                position="1", name="Счётчик однофазный", unit="шт.", quantity="100", lot="1",
                document="Проект контракта.docx", quote="",
            )
        ],
        delivery=_delivery_answer(),
        conditions=ConditionsAnswer(conditions=[]),
    )


def test_one_request_saves_all_three(db_session, client, monkeypatch):
    tender = _tender(db_session)
    calls: list[str] = []

    def fake(db, *, system_prompt, user_text, response_model, **kwargs):
        calls.append(response_model.__name__)
        return _facts()

    monkeypatch.setattr(service, "run_structured", fake)
    found = service.extract_all(db_session, tender)

    assert calls == ["BitrixFactsAnswer"]
    assert found["points"] == 1 and found["stages"] == 1
    assert delivery_terms_service.get_terms(db_session, tender)["supply_model"]["delivery_order"] == "REQUESTS"


def test_failed_combined_request_falls_back_to_separate(db_session, client, monkeypatch):
    """Ответ не по схеме или обрыв — не потеря данных: `None`, и разбор делает три прежних
    запроса; прежние условия на месте."""

    tender = _tender(db_session)
    monkeypatch.setattr(delivery_terms_service, "run_structured", lambda *a, **k: _delivery_answer())
    delivery_terms_service.extract(db_session, tender)

    def boom(*a, **k):
        raise ValueError("ответ не по схеме")

    monkeypatch.setattr(service, "run_structured", boom)
    assert service.extract_all(db_session, tender) is None
    assert len(delivery_terms_service.get_terms(db_session, tender)["points"]) == 1


def test_text_that_does_not_fit_goes_separately(db_session, client, monkeypatch):
    tender = _tender(db_session)
    monkeypatch.setattr(service, "fit_chars", lambda db, desired, reserved: 100)
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert service.extract_all(db_session, tender) is None


def test_positions_are_saved_only_when_documents_have_a_table(db_session, client, monkeypatch):
    tender = _tender(db_session)
    _, present, complete = service.combined_text(db_session, tender, 10**6)
    assert complete and present["delivery"]
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _facts())
    found = service.extract_all(db_session, tender)
    assert found["positions"] == (1 if present["positions"] else 0)
    assert len(tender_positions.list_positions(db_session, tender)) == found["positions"]
