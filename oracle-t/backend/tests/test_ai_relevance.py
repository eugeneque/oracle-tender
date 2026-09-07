"""ИИ-отбор закупок и разбор ОКПД2 (разделы 5.1.1, 5.4 ТЗ).

Сеть не трогаем: вызов модели подменяется. Под наблюдением — решения нашего кода вокруг
ответа модели, где легко начать врать:

* сбой модели не должен превращаться в «закупка нам не подходит»;
* дата в документе не должна становиться кодом ОКПД2;
* подобранное моделью должно оказываться в начале списка.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.source import Source
from app.models.tender import Tender
from app.services import ai_relevance_service
from app.services.ai_relevance_service import RelevanceAnswer
from app.services.tender_analysis import extract_okpd2_from_text


def _tender(db, title: str, **overrides) -> Tender:
    source = Source(
        key=f"airel_{uuid.uuid4().hex[:8]}",
        name="Источник для теста ИИ-отбора",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    # `passed_relevance_filter` — умолчание фабрики, а не жёстко заданное значение: часть
    # тестов проверяет ровно обратный случай.
    fields = {"passed_relevance_filter": True, **overrides}
    tender = Tender(
        source_id=source.id,
        external_id=f"AIR-{uuid.uuid4().hex[:6]}",
        title=title,
        currency="RUB",
        **fields,
    )
    db.add(tender)
    db.flush()
    return tender


# --- ОКПД2: даты больше не становятся кодами ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Тот самый случай: у тендера на метрологические услуги «Дата подведения итогов»
        # давала ОКПД2 23.12 — «стекло обработанное».
        ("Дата подведения итогов: 23.12.2026", None),
        ("Срок выполнения работ по 22.12.2026", None),
        ("Договор от 01.09.2026", None),
        # Настоящие коды при этом должны находиться — в том числе рядом с датами.
        ("Классификация по ОКПД2: 71.12.40.120 Услуги в области метрологии", "71.12.40.120"),
        ("Поставка счетчиков 26.51.63.130 в срок до 15.01.2027", "26.51.63.130"),
        # 71 не бывает днём, 51 не бывает месяцем — это коды, а не даты.
        ("Работы по 71.12.40 и 26.51.63", "26.51.63"),
    ],
)
def test_dates_are_not_mistaken_for_okpd2(text, expected):
    assert extract_okpd2_from_text(text) == expected


# --- ИИ-отбор ------------------------------------------------------------------------------


def _answer(monkeypatch, **fields):
    def fake(db, **kwargs):
        return RelevanceAnswer(**fields)

    monkeypatch.setattr(ai_relevance_service, "run_structured", fake)


def test_relevant_answer_is_stored(db_session, monkeypatch):
    tender = _tender(db_session, "Поставка счетчиков электрической энергии")
    _answer(monkeypatch, is_relevant=True, confidence=95, reason="Предмет — приборы учёта.")

    ai_relevance_service.check_tender(db_session, tender)

    assert tender.ai_relevant is True
    assert tender.ai_relevance_confidence == 95
    assert tender.ai_relevance_reason == "Предмет — приборы учёта."
    assert tender.ai_relevance_checked_at is not None


def test_model_failure_leaves_tender_unchecked(db_session, monkeypatch):
    """Сбой модели — это «не проверяли», а не «не подходит».

    Записать `False` значило бы спрятать закупку из списка из-за нашей же аварии — та же
    ошибка, что ноль вместо «нет данных» в History.
    """

    tender = _tender(db_session, "Поставка счетчиков электрической энергии")

    def boom(db, **kwargs):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(ai_relevance_service, "run_structured", boom)

    assert ai_relevance_service.check_tender(db_session, tender) is None
    assert tender.ai_relevant is None
    assert tender.ai_relevance_checked_at is None


def test_confidence_is_clamped(db_session, monkeypatch):
    """Модель изредка отдаёт значения вне диапазона — в интерфейс они попасть не должны."""

    tender = _tender(db_session, "Поставка счетчиков")
    _answer(monkeypatch, is_relevant=True, confidence=140, reason="Причина.")

    ai_relevance_service.check_tender(db_session, tender)
    assert tender.ai_relevance_confidence == 100


def test_batch_skips_tenders_that_failed_profile(db_session, monkeypatch):
    """Модель смотрит только то, что прошло профиль ключевых слов.

    Иначе вызовы уходили бы на весь поток со всех площадок, включая заведомо чужие закупки, —
    ровно то, от чего предостерегает раздел 5.1.1 ТЗ.
    """

    _tender(db_session, "Поставка канцтоваров", passed_relevance_filter=False)
    _answer(monkeypatch, is_relevant=True, confidence=90, reason="Причина.")

    result = ai_relevance_service.check_batch(db_session, limit=10)

    checked = db_session.scalars(
        select(Tender).where(Tender.ai_relevance_checked_at.is_not(None))
    ).all()
    assert all(item.passed_relevance_filter is True for item in checked)
    assert result.checked == len(checked)


def test_batch_stops_when_model_is_down(db_session, monkeypatch):
    """Подряд идущие сбои останавливают пачку: это недоступная интеграция, а не данные."""

    for index in range(6):
        _tender(db_session, f"Поставка счетчиков электрической энергии {index}")
    db_session.commit()

    def boom(db, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(ai_relevance_service, "run_structured", boom)

    result = ai_relevance_service.check_batch(db_session, limit=6)

    assert result.checked == 0
    assert result.failed == 3
    assert any("Модель недоступна" in message for message in result.messages)


def test_ai_selected_tenders_come_first_by_default(db_session, monkeypatch):
    """Подобранное моделью показывается выше остального при сортировке по умолчанию."""

    from app.services.tender_service import DEFAULT_SORT, TenderFilters, list_tenders

    marker = uuid.uuid4().hex[:8]
    plain = _tender(db_session, f"Поставка счетчиков {marker} без отметки")
    picked = _tender(db_session, f"Поставка счетчиков {marker} подобрана")
    picked.ai_relevant = True
    db_session.commit()

    found = list_tenders(
        db_session,
        limit=10,
        offset=0,
        sort_by=DEFAULT_SORT,
        filters=TenderFilters(search=marker),
    )

    assert [item.id for item in found][:2] == [picked.id, plain.id]
